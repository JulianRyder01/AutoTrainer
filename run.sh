#!/bin/bash

# 确保目录存在
mkdir -p logs
mkdir -p templates

# 如果 templates/dashboard.html 不存在，则创建 (假设上面的HTML内容保存在此文件中)
# 这里假设用户已经把上面的HTML保存为 templates/dashboard.html

echo "Starting AutoTrainer..."
echo "Access Dashboard at http://localhost:8000"

# 启动服务
python AutoTrainer.py