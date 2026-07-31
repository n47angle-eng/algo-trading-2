# Active work orders

目前冇獨立file-based active work order；P6 active order只放channel，避免另一份
file copy漂移。

- P6唯一executor：Agent W。
- P6唯一正式渠道：`AGENT_CHANNEL_W.md`。
- 目前狀態：書面規格及單一implementation plan已完成；W仍HOLD，等Owner轉發
  C短指令啟動`AGENT_CHANNEL_W.md`最新one-shot工作令。
- X／Y共87份已完成work orders已從現行工作樹刪除；Git history仍可恢復。
  W唔自行取回；歷史work order永遠唔係permission。
