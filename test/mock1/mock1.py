# mock_train.py
import os
import sys
import time
import random
import argparse
import uuid
import datetime
from PIL import Image, ImageDraw

def log(msg):
    """带时间戳的标准日志输出，模拟训练日志"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [MockTrain] {msg}", flush=True)

def generate_result_image(text, filename="result.png"):
    """生成包含随机文本的图片"""
    img = Image.new('RGB', (600, 200), color=(73, 109, 137))
    d = ImageDraw.Draw(img)
    # 简单的默认绘制
    d.text((20, 80), text, fill=(255, 255, 0))
    d.text((20, 150), f"Generated at: {datetime.datetime.now()}", fill=(200, 200, 200))
    img.save(filename)
    log(f"Artifact saved to {os.path.abspath(filename)}")

def simulate_gpu_load(size_gb, duration):
    """核心逻辑：占用显存并等待"""
    import torch
    
    if not torch.cuda.is_available():
        log("CRITICAL WARNING: CUDA not available! Running on CPU (Memory test skipped).")
    else:
        device_id = os.environ.get("CUDA_VISIBLE_DEVICES", "Unknown")
        log(f"Visible GPU Devices: {device_id}")
        log(f"Attempting to allocate {size_gb}GB VRAM...")
        
        try:
            # 1GB float32 约等于 2.5 * 10^8 个元素 (1元素=4bytes)
            # 4GB = 10^9 elements
            # 使用 empty 不初始化数据速度更快，ones 初始化会慢一些但更能模拟负载
            num_elements = int(size_gb * (1024**3) / 4)
            
            # 分配大张量
            tensor = torch.ones(num_elements, dtype=torch.float32, device="cuda")
            
            log(f"SUCCESS: Allocated {size_gb}GB VRAM on GPU. Memory is now occupied.")
            log(f"Tensor shape: {tensor.shape}, Device: {tensor.device}")
            
            # 模拟一些计算 (矩阵乘法)，让显卡利用率(Util)也动起来，不仅仅是显存(Mem)
            # 做一个小规模运算防止卡死，主要是为了让 nvidia-smi 看到 usage
            log("Starting dummy computations to spike GPU utilization...")
            start_time = time.time()
            while time.time() - start_time < duration:
                # 做一点点运算
                _ = tensor[:1000] * 2.0 
                
                # 模拟 tqdm 进度条日志（测试 AutoTrainer 的日志过滤功能）
                elapsed = int(time.time() - start_time)
                sys.stdout.write(f"\rTraining: {elapsed}/{duration}s | Loss: {random.random():.4f} | 12.5it/s")
                sys.stdout.flush()
                time.sleep(0.5)
            
            print() # 换行
            
        except RuntimeError as e:
            log(f"CUDA Error: {e}")
            # 如果是测试 OOM 模式，这里会抛出异常
            raise e

def main():
    parser = argparse.ArgumentParser(description="AutoTrainer Mock Script")
    parser.add_argument("--mode", type=str, default="normal", choices=["normal", "oom", "error"], help="运行模式: normal(正常), oom(模拟显存溢出), error(模拟代码报错)")
    parser.add_argument("--mem", type=float, default=4.0, help="占用显存大小(GB)")
    parser.add_argument("--min_time", type=int, default=5, help="最小运行时间(s)")
    parser.add_argument("--max_time", type=int, default=20, help="最大运行时间(s)")
    args = parser.parse_args()

    log("=== Simulation Started ===")
    log(f"Mode: {args.mode.upper()}")
    
    # 1. 模拟环境检查
    # 如果 AutoTrainer 替换了文件，我们可以检查某个标记文件是否存在
    if os.path.exists("config_swapped_marker.txt"):
        log("Detected file swap: config_swapped_marker.txt exists.")

    try:
        # 2. 确定运行时间
        run_duration = random.randint(args.min_time, args.max_time)
        log(f"Task scheduled for {run_duration} seconds.")

        # 3. 不同的测试模式
        if args.mode == "oom":
            log("Simulating Out Of Memory crash...")
            # 尝试分配 80GB 显存，绝大多数单卡都会爆
            simulate_gpu_load(80, run_duration) 
            
        elif args.mode == "error":
            log("Simulating Runtime Error...")
            time.sleep(2)
            simulate_gpu_load(1.0, 2) # 先正常跑一会
            log("Injecting fatal error now!")
            raise ValueError("Simulated unexpected crash for testing retry logic!")
            
        else: # Normal
            simulate_gpu_load(args.mem, run_duration)
            
            # 4. 生成结果
            result_text = f"Task Completed.\nID: {str(uuid.uuid4())[:8]}\nMode: {args.mode}"
            generate_result_image(result_text, filename="result.png")
            log("Main process finished successfully.")

    except Exception as e:
        log(f"Process crashed with error: {e}")
        # 打印详细堆栈供 AutoTrainer 捕获
        import traceback
        traceback.print_exc()
        sys.exit(1) # 非0退出码

if __name__ == "__main__":
    main()