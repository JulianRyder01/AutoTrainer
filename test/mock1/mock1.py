# mock_train.py
import os
import sys
import time
import random
import argparse
import uuid
import datetime
import traceback

# 尝试导入依赖，增强脚本通用性
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

def log(msg):
    """带时间戳的标准日志输出，AutoTrainer 会捕获 stdout"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # flush=True 至关重要，确保日志能实时传输到 AutoTrainer 的 Web 界面
    print(f"[{timestamp}] [MockTrain] {msg}", flush=True)

def generate_result_image(task_name, info_text, filename="result.png"):
    """生成包含任务名和随机文本的图片"""
    if not HAS_PIL:
        log("WARNING: PIL not installed, skipping image generation.")
        return

    width, height = 800, 400
    # 背景色：深蓝色
    img = Image.new('RGB', (width, height), color=(40, 44, 52))
    d = ImageDraw.Draw(img)
    
    # 简单的排版
    # 绘制任务名称 (黄色高亮)
    d.text((20, 20), "AutoTrainer Result Artifact", fill=(100, 100, 100))
    d.text((20, 50), f"TASK: {task_name}", fill=(255, 215, 0)) # Gold color
    
    # 绘制详细信息
    d.text((20, 100), info_text, fill=(200, 200, 200))
    
    # 绘制时间戳
    d.text((20, height - 30), f"Generated at: {datetime.datetime.now()}", fill=(100, 100, 100))
    
    # 绘制一个随机图形增加视觉差异
    color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    d.rectangle([width-100, height-100, width-20, height-20], fill=color)

    try:
        img.save(filename)
        log(f"Artifact saved to {os.path.abspath(filename)}")
    except Exception as e:
        log(f"Failed to save image: {e}")

def simulate_gpu_load(size_gb, duration):
    """核心逻辑：占用显存并等待"""
    if not HAS_TORCH:
        log("CRITICAL WARNING: PyTorch not installed! Running in CPU-only dummy mode.")
        time.sleep(duration)
        return

    if not torch.cuda.is_available():
        log("CRITICAL WARNING: CUDA not available! Running on CPU (Memory test skipped).")
        time.sleep(duration)
    else:
        # 获取环境变量中的 CUDA 设定
        device_id = os.environ.get("CUDA_VISIBLE_DEVICES", "Unknown")
        log(f"Visible GPU Devices: {device_id}")
        log(f"Attempting to allocate {size_gb}GB VRAM...")
        
        try:
            # 1GB float32 约等于 2.5 * 10^8 个元素 (1元素=4bytes)
            num_elements = int(size_gb * (1024**3) / 4)
            
            # 分配大张量
            tensor = torch.ones(num_elements, dtype=torch.float32, device="cuda")
            
            log(f"SUCCESS: Allocated {size_gb}GB VRAM on GPU. Memory is now occupied.")
            log(f"Tensor shape: {tensor.shape}, Device: {tensor.device}")
            
            log("Starting dummy computations to spike GPU utilization...")
            start_time = time.time()
            
            # 模拟训练循环
            step = 0
            while time.time() - start_time < duration:
                step += 1
                # 做矩阵乘法让 GPU Util 动起来
                if step % 10 == 0:
                     _ = torch.matmul(tensor[:1000], tensor[:1000])
                
                # 模拟 tqdm 进度条日志 (AutoTrainer 界面会过滤掉这些高频刷新，但邮件会保留最后几行)
                elapsed = int(time.time() - start_time)
                # 使用 \r 回车符模拟进度条更新
                sys.stdout.write(f"\rEpoch 1/1 | Step {step} | Time: {elapsed}/{duration}s | Loss: {random.random():.4f}")
                sys.stdout.flush()
                time.sleep(0.5)
            
            print() # 进度条结束后换行
            
        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                log("Caught Expected CUDA OOM Exception inside script.")
            raise e

def main():
    parser = argparse.ArgumentParser(description="AutoTrainer Mock Script")
    # [新增] 接收任务名称
    parser.add_argument("--name", type=str, default="Unnamed_Task", help="任务名称，由AutoTrainer传入")
    parser.add_argument("--mode", type=str, default="normal", choices=["normal", "oom", "error"], help="运行模式")
    parser.add_argument("--mem", type=float, default=2.0, help="占用显存大小(GB)")
    parser.add_argument("--min_time", type=int, default=5, help="最小运行时间(s)")
    parser.add_argument("--max_time", type=int, default=15, help="最大运行时间(s)")
    
    args = parser.parse_args()

    log("=== Simulation Started ===")
    log(f"Task Name: {args.name}")
    log(f"Mode: {args.mode.upper()}")
    
    # 1. 模拟环境检查 (测试文件替换功能)
    if os.path.exists("config_swapped_marker.txt"):
        log("Detected file swap: config_swapped_marker.txt exists.")

    try:
        # 2. 确定运行时间
        run_duration = random.randint(args.min_time, args.max_time)
        log(f"Task scheduled for {run_duration} seconds.")

        # 3. 执行逻辑
        if args.mode == "oom":
            log("Simulating Out Of Memory crash...")
            # 尝试分配极大显存
            simulate_gpu_load(80, run_duration) 
            
        elif args.mode == "error":
            log("Simulating Runtime Error...")
            time.sleep(2)
            simulate_gpu_load(0.5, 2) 
            log("Injecting fatal error now!")
            raise ValueError("Simulated unexpected crash for testing retry logic!")
            
        else: # Normal
            simulate_gpu_load(args.mem, run_duration)
            
            # 4. 成功结束，生成结果
            result_info = f"ID: {str(uuid.uuid4())[:8]}\nMode: {args.mode}\nMem: {args.mem}GB\nDuration: {run_duration}s"
            # 传入任务名用于绘图
            generate_result_image(args.name, result_info, filename=f"result_{args.name}.png")
            log(f"Task '{args.name}' finished successfully.")

    except Exception as e:
        log(f"Process crashed with error: {e}")
        # 打印详细堆栈供 AutoTrainer 捕获并发送邮件
        traceback.print_exc()
        sys.exit(1) # [关键] 非0退出码通知 AutoTrainer 任务失败

if __name__ == "__main__":
    main()