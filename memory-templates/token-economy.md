# Token Economy / 限量管理策略

**用途**：让 CC 在 5 小时滚动窗口 + 周限量约束下，最大化"上线 + 变现"产出。

**用户偏好**（已在 memory）：
- 5 小时限量到了 → 循环检视 + ScheduleWakeup 重置时继续
- 减少 macOS CI 触发频次（每次 12 分钟队列）
- 效率第一；最终目的：不浪费订阅最大化变现

---

## 触发等待 / Idle 信号检测

CC **看不到精确剩余 token**，但可以从这些信号推断接近限量：
- 工具调用反复返回 throttle / rate-limit 错误
- 用户 quota 报错
- 模型显式说 "context too long" / cache miss

正常工作时这些都不出现。出现一次即视为接近软上限，立刻进**保守模式**。

---

## 三档操作模式

### Active 模式（正常）
- 自由写代码、edit 文件、commit、push
- 跑 CI 验证小步迭代
- 用 background bash 等长任务
- 不积压未 push 的本地改动 > 1 份

### Conservative 模式（接近限量）
- **不再 push 触发 macOS CI**（每次 12 分钟、机器时间贵）
- 多个改动 batch 起来，一次 commit
- 文件改动累积到本地，等 Active 模式恢复再批量 push
- 只读操作（gh run list / git log / Read tool）继续，但不主动 fetch 大文件
- 不跑 Playwright（headed 浏览器 + 大量截图浪费 vision token）

### Recovery 模式（限量到了）
- **不再调任何工具**
- 立刻 ScheduleWakeup 到 `5 小时窗口重置时间 + 60s` (避免边缘 race)
- prompt 设为继续之前 task，**不要重新摘要 / 重新规划**（浪费 token）
- 把当前未完成的具体下一步写到 RESUME.md 的"in-flight"段（让重新进入时不丢上下文）

---

## CI 触发频次优化

每个 push 触发 12 分钟 macOS CI。频繁 push 是 token + 用户耐心的双重浪费。

### 应该 batch 的改动（一次 push）
- 同一仓多文件 small fixes
- 跨仓 metadata 文本更新（不影响 build 但触发 CI）
- 文档更新（虽然 .md 已被 paths-ignore）

### 应该立刻 push 的改动（即使触发 CI）
- 修 build 错误 / test 失败 — 验证比 batch 重要
- 涉及上线流程关键 path 的代码 — 早发现问题早修
- 用户明确请求验证的改动

### 用 paths-ignore 减少触发
现在 4 仓的 `.github/workflows/ci.yml` 只有 `paths-ignore: ["**/*.md"]`。可加：

```yaml
paths-ignore:
  - "**/*.md"
  - "fastlane/metadata/**"      # 改 metadata 不改 build
  - "fastlane/screenshots/**"   # 改截图不改 build
  - ".github/ISSUE_TEMPLATE/**"
  - ".github/PULL_REQUEST_TEMPLATE.md"
  - "PRIVACY.md"
  - "SECURITY.md"
  - "README.md"
  - "docs/**"
```

**但加这个改动本身又会触发一次 CI** —— 应该在 Active 模式 + 有顺势其它 source code 改动时一起 push。

---

## 何时使用 Background Bash vs Foreground

- **Foreground**：< 30 秒预估
- **Background + Monitor**：30 秒-5 分钟有连续日志可监听（builds, log streams）
- **Background + ScheduleWakeup**：5 分钟+ 等不重要事件（CI 完成、Apple email 到达）。**不要主动 poll**

---

## 何时使用 Subagent（Agent 工具）

参 system prompt：spawn cold，每次重新 derive context 浪费 token。

适合 spawn 子 agent：
- 跨 5+ 文件的大型 search-and-summary（"找所有 IAP 相关代码 + 报告差异"）
- 需要并行的独立查询（与 dependency 分析并行的另一条审计 path）
- 需要长 thinking 的分析任务（不让主 context 被 derail）

**不要** spawn 子 agent：
- 简单 grep / file read（直接用 Grep/Read 工具）
- 已知答案的 yes/no
- 用户问的具体小问题
- 任何能在主 context 里 5 分钟内做完的事

---

## 限量到时的具体动作（Recovery 模式 runbook）

1. 写一行进当前正在做的 todo（比如 "等 CI on commit X 完成 → 继续 Y"）
2. 简短告诉用户：限量到了，已设置 wakeup 在 {{time}} 重新进入继续 Y
3. 调 ScheduleWakeup 设到下次 5 小时窗口重置 + 60 秒；prompt 设为重新进入这个 task
4. **不再调任何其他工具**

具体 ScheduleWakeup 调用模式：
```
delaySeconds: <剩余到 reset 时间 + 60>
reason: "Claude Pro 5h window reset + 60s buffer to avoid race"
prompt: "继续 AutoApp portfolio 工作，从 RESUME.md 的 in-flight 段恢复"
```

---

## 用户在睡觉 / 不在线时的策略

- 用户不在 = CC 自由 batch + 减少触发 + 文档化
- 不在线时**别 push 高风险改动**（force push / 删 branch / 大重构）— 万一出错没人喊停
- 用 background bash + 长 timeout 跑非阻塞工作；醒来一次性看结果
- 5 小时窗口结束前留 5 分钟把 RESUME.md 状态写新

---

## 最大化变现的 token 分配优先级

按下面顺序花 token（最重要的先做）：

1. **修 build break / test fail**（任何 token 都不嫌多）
2. **上线前硬要求 polish**（metadata / privacy / icon / 提审说明）
3. **上线日营销稿件 + 7 天素材库**（一次写完一辈子用）
4. **跨平台数据采集 + 信号驱动决策**（一次做完累积资产）
5. **Backlog polish**（icon v2 / starter pack 200 条目标 / Apple Watch companion）
6. **学习 / 探索性工作**（评估新 App 候选）

5 小时窗口快到时，从 6 → 1 倒序停手。

---

## Memory 写入策略

每个 5 小时窗口结束前 30 分钟（如果 CC 检测到自己已工作 4.5 小时），自动：
- 把这个窗口里的关键 decision 写进 `decisions.md`
- 把当前正在做的任务写到 `RESUME.md` "in-flight" 段
- 不重新写整体 summary（浪费 token）

下一个 5 小时窗口重新进入时，先 read MEMORY.md → RESUME.md → state.yml，**不**重新 grep 全 codebase。
