# What is AutoTrainer?
AutoTrainer provide a queue for you to plan your training tasks.

Monitoring the GPUs and automatically set the card of your training tasks, then start it.

Auto-Retry and Send Email to you about Trainning Statues (with our outra email system -- EMinder, also published on GitHub)

# Getting Started
1. Run `conda create -n AutoTrainer python==3.10`
2. Activate the environment with `conda activate AutoTrainer`
3. Install the requirements with `pip install -r requirements.txt`

# AutoTrainer Dev Manual

You can do as follow so that your script can be integrated with AutoTrainer.

It can receive parameters, set the card of your training tasks, and start it.


本文档旨在指导算法工程师开发可与 **AutoTrainer Pro** 自动化系统无缝对接的训练脚本。

## 1. 核心契约 (Interface Contract)

AutoTrainer 通过标准的 **Shell 命令行** 调用你的 Python 脚本。为了确保任务状态能被正确监控、日志能被记录、失败能被重试，你的脚本需要遵循以下规范。

### 1.1 接收参数
建议你的脚本使用 `argparse` 接收参数。AutoTrainer 前端表单中填写的“启动命令”将直接传递给 Shell。

**示例：**
在 AutoTrainer 中填写命令：
```bash
python train.py --config configs/yolo_v8.yaml --batch_size 16 --name "Experiment_001"
```

在 `train.py` 中：
```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--name", type=str, help="任务名称，建议接收此参数用于日志或文件命名")
# ... 其他参数
args = parser.parse_args()
```

### 1.2 环境变量 (显卡控制)
AutoTrainer 会自动管理显卡资源。在启动你的脚本前，它会设置 `CUDA_VISIBLE_DEVICES` 环境变量。

*   **开发者行为**：**不要**在代码中硬编码 `device = 'cuda:0'`。
*   **推荐做法**：直接使用 `device = 'cuda'` 或 `cuda:0`。由于 AutoTrainer 已经屏蔽了其他显卡，代码看到的 `cuda:0` 实际上是系统分配给该任务的那张特定空闲显卡（例如物理 ID 7）。

### 1.3 退出码 (Exit Codes) **[重要]**
AutoTrainer 依靠进程的退出码判断任务状态：
*   **Exit Code 0**: 任务成功 (Completed)。
*   **Exit Code != 0**: 任务失败 (Failed)，AutoTrainer 会根据配置尝试重试。

**代码规范：**
```python
import sys
try:
    # 训练逻辑
    pass
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1) # 显式返回非0状态码
```

## 2. 日志与监控

AutoTrainer 会实时捕获脚本的 `stdout` (标准输出) 和 `stderr` (错误输出)。

### 2.1 实时日志
为了确保 Web 界面能实时看到日志滚动，请确保打印时**刷新缓冲区**。

*   **Python `print`**: 使用 `flush=True`。
    ```python
    print(f"Epoch {epoch} finished.", flush=True)
    ```
*   **Logging 模块**: 配置 StreamHandler。
*   **tqdm 进度条**: AutoTrainer 已经处理了 `\r` 刷新，可以正常使用 `tqdm`，但建议不要刷新频率过高（AutoTrainer 邮件系统会自动过滤掉纯进度条日志，保留关键信息）。

### 2.2 显存溢出 (OOM) 检测
AutoTrainer 会扫描日志流。如果发现以下不区分大小写的关键词，会标记为 OOM 并在邮件中高亮提醒：
*   `out of memory`
*   `cuda out of memory`

## 3. 文件系统与工作目录

### 3.1 工作目录
AutoTrainer 启动时会 `cwd` (cd) 到你指定的工作目录。所有的相对路径（如读取 `data/`，保存 `checkpoints/`）都将基于该目录。

### 3.2 结果产出
建议将生成的模型权重、Loss 曲线图、验证集结果图保存在工作目录下。
*   AutoTrainer 的邮件通知系统目前支持发送**日志文件**作为附件。
*   (进阶) 如果需要发送图片附件，需修改 AutoTrainer 源码中的 `EminderClient` 调用逻辑，或者将图片路径输出在日志中供人工查阅。

### 3.3 配置文件替换 (Hot-Swap)
AutoTrainer 支持“模块文件替换”功能。
*   **场景**：你想测试一个新的 `loss.py`，但不想修改原始代码库。
*   **机制**：任务开始前，AutoTrainer 会把你的临时文件覆盖到目标位置；任务结束后（无论成功失败），会自动还原原始文件。
*   **注意**：请确保你的代码支持热重载（通常脚本每次重新启动进程，不需要特殊处理）。

## 4. 最小可用模板 (Boilerplate)

```python
import os
import sys
import argparse
import datetime
import traceback

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", type=str, required=True)
    parser.add_argument("--epochs", type=int, default=10)
    args = parser.parse_args()

    print(f"[{datetime.datetime.now()}] Starting Job: {args.name}", flush=True)
    
    # 检查显卡环境
    print(f"CUDA_VISIBLE_DEVICES: {os.environ.get('CUDA_VISIBLE_DEVICES')}", flush=True)

    try:
        # --- 训练主循环 ---
        for epoch in range(args.epochs):
            # simulate training
            loss = 0.1 * (10 - epoch)
            print(f"Epoch {epoch+1}/{args.epochs} | Loss: {loss:.4f}", flush=True)
            
            # 模拟 OOM 风险点
            # if epoch == 5: raise RuntimeError("CUDA out of memory")
        
        # --- 保存结果 ---
        with open("result.txt", "w") as f:
            f.write(f"Task {args.name} Finished.")
            
        print("Training Completed Successfully.", flush=True)
        sys.exit(0)

    except Exception:
        traceback.print_exc() # 打印堆栈以便调试
        sys.exit(1) # 报错退出

if __name__ == "__main__":
    main()
```

### 如何测试新的 Mock 脚本？

1.  保存上面的 `mock_train.py`。
2.  启动 `AutoTrainer.py`。
3.  打开浏览器 `http://localhost:8000`。
4.  新建任务：
    *   **任务名称**: `Mock_Test_V1`
    *   **启动命令**: `python mock_train.py --name "Mock_Test_V1" --mode normal --mem 2.0`
    *   **工作目录**: `.` (当前目录)
    *   **显卡**: Min 1, Max 1
5.  提交后，观察：
    *   Web 界面状态变为 Running。
    *   点击 "查看日志" (或者直接看本地 logs 文件夹)，确认可以看到 `Task Name: Mock_Test_V1`。
    *   任务完成后，当前目录下应该会出现 `result_Mock_Test_V1.png`，打开图片，上面会有黄色的任务名。
    *   如果您配置了EMinder服务，还可以发送邮件给您的邮箱。