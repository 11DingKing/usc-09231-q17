# 独立基础项目

这是从上游真实项目父版本整理出的独立 Python 基础快照，保留复现缺陷所需的源码、测试和配置。

## 依赖

```bash
python3 -m venv .venv
.venv/bin/pip install Pillow pytest
```

运行测试：

```bash
.venv/bin/python -m pytest
# 或用 unittest（会跑 load_tests 生成的全矩阵）：
.venv/bin/python -m unittest discover -s tests -t .
```

检查构建：

```bash
.venv/bin/python -m compileall vncdotool tests
```

## 结构

- `vncdotool/player.py` — 场景播放器：发送按键/点击事件并抓取帧缓冲
- `vncdotool/command.py` — `vncdo` 命令行入口（`python3 -m vncdotool`）
- `vncdotool/pixelformat.py` — 服务可能宣告的像素格式
- `tests/goldens/` — 场景、字形与点击目标的黄金定义
- `tests/functional/xservice.py` — 模拟 X 服务：异步绘制帧缓冲
- `tests/functional/utils.py` — 测试舰队：模拟服务的启动、探测与 `run_vncdo`

## 播放器与 X 服务的同步/重试边界

X 服务异步绘制：播放器连接时帧缓冲可能仍是未绘制的空白帧，直接抓取会把竞态中的半成品（偶尔是纯空白帧）保存为结果。因此：

- **首次绘制同步**：会话内第一次抓取前，播放器轮询服务的 `status`，直到 `ready=1`（首次绘制完成）；
- **抓取重试**：抓取结果为单色（空白）帧时视为尚未绘制，按有界次数重试；
- **清晰失败**：
  - 服务提前退出（连接被拒绝或中途断开）→ `ServerExitedError`，CLI 退出码 3；
  - 重试耗尽（始终未 ready 或帧始终空白）→ `RetryExhaustedError`，CLI 退出码 4。

模拟 X 服务支持延迟与故障注入（构造参数，见 `tests/functional/xservice.py`）：
`startup_delay`、`draw_delay`、`blank_captures`、`die_after_captures`、`never_ready`。

手动启动模拟舰队：

```bash
.venv/bin/python -m tests.functional.utils
```
