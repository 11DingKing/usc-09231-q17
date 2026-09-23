# 独立基础项目

这是从上游真实项目父版本整理出的独立 Python 基础快照，保留复现缺陷所需的源码、测试和配置。

## 背景：帧缓冲抓取竞态

场景播放器（`scene_player/`）向 X 服务发送按键事件后抓取帧缓冲。X 服务的绘制是
**异步**的：`press_key` 返回时画面未必上屏，此时抓取会得到：

- 未初始化的单色缓冲（空白帧）；
- 擦屏后、提交前的半成品；
- 上一个场景的旧画面。

修复后的同步边界在 `scene_player/player.py`：

1. 按键后**轮询**帧缓冲，只有「非单色 **且** 角标 stamp 与请求按键一致」的帧才被接受，
   即严格等待本次绘制的首个成品帧；空白帧、半成品帧、旧帧一律重试，绝不落盘。
2. 结果文件通过 `*.part` + `os.replace` **原子提交**，任何失败路径下都不会留下半成品文件。
3. 三类失败给出明确结果：
   - `ServiceNotRunningError`：服务尚未启动就请求抓取；
   - `ServiceExitedError`：等待期间服务提前退出（携带退出码与末次帧状态）；
   - `RetryExhaustedError`：重试耗尽（`max_attempts` 或 `timeout`，携带末次拒绝原因）。
4. 绘制延迟、绘制失败（留下白屏）、瞬时抓取失败、时钟均可注入，见 `test_sync/`。

## 运行测试

离线、零第三方依赖的竞态回归套件：

```bash
python3 -m unittest discover -t . -s test_sync -v
```

上游套件需要 Pillow / vncdotool 与真实 VNC 舰队环境：

```bash
python3 -m pytest
```

检查构建：

```bash
python3 -m compileall .
```
