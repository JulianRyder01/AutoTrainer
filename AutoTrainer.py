# AutoTrainer.py
import os
import sys
import time
import json
import shutil
import logging
import subprocess
import threading
import datetime
import traceback
import signal
import requests
import warnings
import glob
from typing import List, Dict, Optional
from enum import Enum

# 忽略 pynvml 的 FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")

import pynvml
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, desc
from sqlalchemy.orm import sessionmaker, declarative_base

# ==============================================================================
# 0. 内嵌资源 (前端修复核心区域)
# ==============================================================================
# [修复说明]
# 1. 移除了 modal 的 'fade' 类，防止弹窗透明不可见。
# 2. 增加了 v-cloak 防止 Vue 加载前的闪烁。
# 3. 增强了 Modal 的 CSS 样式，确保它一定显示在最上层。
# 4. JS中将 .then(() => 改为 .then(_ => 以防止出现 '((', 
#    避免与 Python 后端 Jinja2 的 variable_start_string='((' 发生冲突。
HTML_TEMPLATE_CONTENT = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AutoTrainer 控制台</title>
    <!-- 使用 jsDelivr 加载依赖 -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/vue@2.6.14/dist/vue.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/axios@1.3.4/dist/axios.min.js"></script>
    <style>
        [v-cloak] { display: none; } /* 防止 Vue 加载前显示花括号 */
        body { background-color: #f4f6f9; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .card { border: none; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-radius: 8px; }
        .status-badge { min-width: 80px; display: inline-block; text-align: center; }
        
        /* 状态配色 */
        .status-running { background-color: #e3f2fd; color: #0d6efd; border: 1px solid #0d6efd; }
        .status-pending { background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; }
        .status-completed { background-color: #d1e7dd; color: #0f5132; border: 1px solid #badbcc; }
        .status-failed { background-color: #f8d7da; color: #842029; border: 1px solid #f5c6cb; }
        .status-stopped { background-color: #e2e3e5; color: #41464b; border: 1px solid #d3d6d8; }
        /* [新增] 暂停状态样式 */
        .status-paused { background-color: #f8f9fa; color: #6c757d; border: 1px solid #dee2e6; border-style: dashed; }

        /* [关键修复] 自定义 Modal 样式，不依赖 Bootstrap JS */
        .custom-modal-backdrop {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background-color: rgba(0, 0, 0, 0.5);
            z-index: 1050;
            display: flex;
            justify-content: center;
            align-items: flex-start; /* 防止长弹窗无法滚动 */
            overflow-y: auto;
            padding-top: 50px;
            padding-bottom: 50px;
        }
        .custom-modal-content {
            background: white;
            border-radius: 8px;
            width: 100%;
            max-width: 800px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.3);
            position: relative;
            z-index: 1051;
            margin: auto;
        }
        
        /* [新增] 日志查看弹窗特别样式 */
        .log-modal-content {
            max-width: 90%;
            height: 85vh;
            display: flex;
            flex-direction: column;
        }
        .log-viewer {
            background-color: #1e1e1e;
            color: #d4d4d4;
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            padding: 15px;
            overflow-y: auto;
            flex-grow: 1;
            white-space: pre-wrap;
            border-bottom-left-radius: 8px;
            border-bottom-right-radius: 8px;
            font-size: 0.9rem;
        }

        .btn-group-xs > .btn, .btn-xs {
            padding: .25rem .4rem;
            font-size: .875rem;
            line-height: 1.5;
            border-radius: .2rem;
        }
    </style>
</head>
<body>
    <!-- v-cloak 确保 Vue 加载完成前不显示乱码 -->
    <div id="app" class="container-fluid py-4 px-4" v-cloak>
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h2 class="fw-bold text-primary"><span style="color:#333">Auto</span>Trainer <small class="text-muted fs-6">Pro Edition</small></h2>
            <button class="btn btn-primary btn-lg shadow-sm" @click="openModal(null)">
                <span style="font-size: 1.1rem; font-weight: bold;">+ 新建训练任务</span>
            </button>
        </div>

        <!-- 统计面板 -->
        <div class="row g-3 mb-4">
            <div class="col-md-2">
                <div class="card p-3 text-center">
                    <div class="text-muted small">排队中 (Pending)</div>
                    <div class="fs-2 fw-bold text-warning">{{ stats.pending }}</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="card p-3 text-center">
                    <div class="text-muted small">运行中 (Running)</div>
                    <div class="fs-2 fw-bold text-primary">{{ stats.running }}</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="card p-3 text-center">
                    <div class="text-muted small">近30日完成</div>
                    <div class="fs-2 fw-bold text-success">{{ stats.success_30d }}</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="card p-3 text-center">
                    <div class="text-muted small">近30日失败</div>
                    <div class="fs-2 fw-bold text-danger">{{ stats.failed_30d }}</div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card p-3" style="height: 100%;">
                    <div class="text-muted small mb-2">GPU 实时监控 (阈值: 2000MB)</div>
                    <div class="row g-2" style="max-height: 160px; overflow-y: auto;">
                        <div class="col-6" v-for="gpu in gpus" :key="gpu.id">
                            <div class="border rounded p-1 small d-flex justify-content-between align-items-center" 
                                 :class="gpu.is_free ? 'bg-light text-success' : 'bg-light text-danger'">
                                <span>GPU {{gpu.id}}</span>
                                <span>{{gpu.mem_used}}M / {{gpu.util}}%</span>
                            </div>
                        </div>
                        <div v-if="gpus.length === 0" class="text-muted small text-center w-100 mt-2">
                            未检测到 GPU 或 驱动未安装
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- 任务列表 -->
        <div class="row">
            <div class="col-lg-12">
                <div class="card">
                    <div class="card-header bg-white py-3">
                        <h5 class="mb-0">任务队列</h5>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-hover align-middle mb-0">
                            <thead class="table-light">
                                <tr>
                                    <th width="5%">ID</th>
                                    <th width="20%">任务名称/命令</th>
                                    <th width="10%">状态</th>
                                    <th width="10%">配置</th>
                                    <th width="15%">时间</th>
                                    <th width="20%">详情</th>
                                    <th width="20%">操作</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="task in tasks" :key="task.id" :class="{'table-active': task.status === 'running'}">
                                    <td>#{{ task.id }}</td>
                                    <td>
                                        <div class="fw-bold">{{ task.name }}</div>
                                        <div class="text-muted small text-truncate" style="max-width: 250px;" :title="task.command">{{ task.command }}</div>
                                    </td>
                                    <td>
                                        <span class="badge rounded-pill status-badge" :class="'status-' + task.status">
                                            {{ task.status }}
                                        </span>
                                        <div v-if="task.status === 'running'" class="small text-primary mt-1">PID: {{ task.pid }}</div>
                                    </td>
                                    <td>
                                        <div class="small">GPU: {{ task.gpu_config.min_gpus }}~{{ task.gpu_config.max_gpus }}</div>
                                        <div v-if="task.retry_count > 0" class="small text-danger">Retry: {{task.retry_count}}</div>
                                    </td>
                                    <td class="small">
                                        <div v-if="task.started_at">始: {{ formatTime(task.started_at) }}</div>
                                        <div v-if="task.finished_at">终: {{ formatTime(task.finished_at) }}</div>
                                        <div v-if="!task.started_at" class="text-muted">等待中...</div>
                                    </td>
                                    <td>
                                        <div v-if="task.artifact_dir" class="small text-success mb-1" title="产物抓取开启">
                                            📸 产物: {{ task.artifact_pattern || '*' }}
                                        </div>
                                        <div v-if="task.file_swaps && task.file_swaps.length > 0" class="small text-muted">
                                            📄 {{ task.file_swaps.length }} 个文件替换
                                        </div>
                                        <div v-if="task.status==='failed' || task.status==='completed' || task.status==='stopped'" class="small">
                                            Exit: {{ task.exit_code }}
                                            <span v-if="task.log_file_path" class="ms-1" title="日志已保存">📝</span>
                                        </div>
                                        <div v-if="task.error_msg" class="text-danger small text-truncate" style="max-width: 200px;" :title="task.error_msg">
                                            {{ task.error_msg }}
                                        </div>
                                    </td>
                                    <td>
                                        <div class="d-flex flex-wrap gap-1">
                                            <!-- [新增] 日志查看按钮 (有日志路径即可看) -->
                                            <button v-if="task.log_file_path || task.status === 'running'" class="btn btn-sm btn-outline-dark" @click="viewLog(task.id)" title="查看日志">
                                                📜 日志
                                            </button>

                                            <!-- 开始按钮 (仅 Paused) -->
                                            <button v-if="task.status === 'paused'" class="btn btn-sm btn-success" @click="startTask(task.id)">
                                                ▶ 开始
                                            </button>

                                            <!-- 停止按钮 (仅 Running/Pending) -->
                                            <button v-if="task.status === 'running' || task.status === 'pending'" 
                                                    class="btn btn-sm btn-outline-warning" @click="stopTask(task.id)">⏹ 停止</button>
                                            
                                            <!-- 编辑按钮 (非 Running) -->
                                            <button v-if="task.status !== 'running'" class="btn btn-sm btn-outline-primary" @click="openModal(task)">
                                                ✎ 编辑
                                            </button>

                                            <!-- 复制按钮 (所有) -->
                                            <button class="btn btn-sm btn-outline-secondary" @click="copyTask(task.id)">
                                                📋 复制
                                            </button>

                                            <!-- 重试按钮 (Failed/Completed/Stopped) -->
                                            <button v-if="['failed', 'completed', 'stopped'].includes(task.status)" 
                                                    class="btn btn-sm btn-outline-info" @click="retryTask(task.id)">
                                                🔄 重试
                                            </button>

                                            <!-- 删除按钮 -->
                                            <button class="btn btn-sm btn-outline-danger" @click="delTask(task.id)">🗑</button>
                                        </div>
                                    </td>
                                </tr>
                                <tr v-if="tasks.length === 0">
                                    <td colspan="7" class="text-center py-5 text-muted">
                                        <h4>📭</h4>
                                        <div>当前无任务，点击右上角新建</div>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- 新建/编辑任务弹窗 -->
        <div class="custom-modal-backdrop" v-if="showModal" @click.self="showModal = false">
            <div class="custom-modal-content">
                <div class="modal-header p-3 border-bottom d-flex justify-content-between">
                    <h5 class="modal-title mb-0">{{ editingId ? '编辑任务' : '创建新训练任务' }}</h5>
                    <button type="button" class="btn-close" @click="showModal = false"></button>
                </div>
                <div class="modal-body p-4">
                    <form @submit.prevent="submitTask">
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <label class="form-label fw-bold">任务名称 <span class="text-danger">*</span></label>
                                <input type="text" class="form-control" v-model="form.name" required placeholder="例如: Baseline_V1">
                            </div>
                            <div class="col-md-6">
                                <label class="form-label fw-bold">工作目录 (Git Root) <span class="text-danger">*</span></label>
                                <input type="text" class="form-control" v-model="form.working_dir" required placeholder="/path/to/project">
                            </div>
                        </div>

                        <div class="mb-3">
                            <label class="form-label fw-bold">启动命令 <span class="text-danger">*</span></label>
                            <div class="text-muted small mb-1">提示：支持 "conda activate env && python script.py" (Linux)</div>
                            <textarea class="form-control font-monospace bg-light" v-model="form.command" rows="3" required placeholder="conda activate MyEnv && python train.py"></textarea>
                        </div>

                        <!-- 产物抓取配置 -->
                        <div class="mb-3 p-3 bg-light border rounded">
                            <label class="form-label fw-bold text-success">📸 结果产物自动发送 (Artifacts)</label>
                            <div class="row">
                                <div class="col-md-8">
                                    <input type="text" class="form-control" v-model="form.artifact_dir" placeholder="输出文件目录 (绝对路径, 留空则不抓取)">
                                    <div class="form-text">产物目录</div>
                                </div>
                                <div class="col-md-4">
                                    <input type="text" class="form-control" v-model="form.artifact_pattern" placeholder="文件名模式 (默认 *.jpg)">
                                </div>
                            </div>
                        </div>

                        <div class="row mb-3 border rounded mx-1 p-2">
                            <div class="col-md-4">
                                <label class="form-label">最小显卡数</label>
                                <input type="number" class="form-control" v-model="form.min_gpus" min="1">
                            </div>
                            <div class="col-md-4">
                                <label class="form-label">最大显卡数</label>
                                <input type="number" class="form-control" v-model="form.max_gpus" min="1">
                            </div>
                            <div class="col-md-4">
                                <label class="form-label">失败重试次数</label>
                                <input type="number" class="form-control" v-model="form.retry_count" min="0" value="1">
                            </div>
                        </div>

                        <div class="mb-3">
                            <label class="form-label d-flex justify-content-between align-items-center">
                                <span class="fw-bold">🧩 模块文件替换 (可选)</span>
                                <button type="button" class="btn btn-sm btn-outline-primary" @click="addSwap">+ 添加替换对</button>
                            </label>
                            <div v-for="(swap, idx) in form.swaps" :key="idx" class="input-group mb-2">
                                <span class="input-group-text bg-white">源</span>
                                <input type="text" class="form-control" v-model="swap.source" placeholder="Source Path">
                                <span class="input-group-text bg-white">➔ 目标</span>
                                <input type="text" class="form-control" v-model="swap.target" placeholder="Target Path">
                                <button type="button" class="btn btn-outline-danger" @click="form.swaps.splice(idx, 1)">×</button>
                            </div>
                            <div v-if="form.swaps.length === 0" class="text-muted small">无文件替换操作 (通常用于临时修改代码文件)</div>
                        </div>

                        <div class="modal-footer px-0 pb-0 pt-3 border-top">
                            <button type="button" class="btn btn-secondary me-2" @click="showModal = false">取消</button>
                            <button type="submit" class="btn btn-primary px-4">{{ editingId ? '保存更改' : '提交任务' }}</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <!-- [新增] 日志查看弹窗 -->
        <div class="custom-modal-backdrop" v-if="showLogModal" @click.self="showLogModal = false">
            <div class="custom-modal-content log-modal-content">
                <div class="modal-header p-3 border-bottom d-flex justify-content-between">
                    <h5 class="modal-title mb-0">📜 运行日志 (Tail) - Task #{{ currentLogTaskId }}</h5>
                    <button type="button" class="btn-close" @click="showLogModal = false"></button>
                </div>
                <div class="log-viewer" ref="logContainer">
                    <div v-if="logLoading" class="text-center text-muted">加载中...</div>
                    <div v-else-if="logContent">{{ logContent }}</div>
                    <div v-else class="text-center text-muted">暂无日志内容</div>
                </div>
                <div class="modal-footer p-2 bg-light border-top">
                    <button class="btn btn-sm btn-secondary" @click="fetchLog(currentLogTaskId)">刷新</button>
                    <button class="btn btn-sm btn-primary" @click="showLogModal = false">关闭</button>
                </div>
            </div>
        </div>

    </div>

    <script>
        new Vue({
            el: '#app',
            data: {
                stats: { pending: 0, running: 0, success_30d: 0, failed_30d: 0 },
                tasks: [],
                gpus: [],
                gpu_threshold: 0,
                showModal: false,
                showLogModal: false, // [新增]
                logContent: '',      // [新增]
                logLoading: false,   // [新增]
                currentLogTaskId: null, // [新增]
                editingId: null, 
                form: {
                    name: '',
                    command: '',
                    working_dir: '',
                    min_gpus: 1,
                    max_gpus: 8,
                    retry_count: 1,
                    artifact_dir: '',
                    artifact_pattern: '',
                    swaps: []
                }
            },
            methods: {
                loadData() {
                    axios.get('/api/stats').then(res => {
                        this.stats = res.data.stats;
                        this.gpus = res.data.gpus;
                        this.tasks = res.data.tasks;
                    }).catch(console.error);
                },
                openModal(task) {
                    if (task) {
                        // 编辑模式
                        if (['completed', 'failed', 'stopped'].includes(task.status)) {
                            if (!confirm("编辑已完成或停止的任务将重新加入队列并重置状态，确定要继续吗？")) {
                                return;
                            }
                        }
                        this.editingId = task.id;
                        this.form = {
                            name: task.name,
                            command: task.command,
                            working_dir: task.working_dir,
                            min_gpus: task.gpu_config.min_gpus,
                            max_gpus: task.gpu_config.max_gpus,
                            retry_count: task.max_retries,
                            artifact_dir: task.artifact_dir || '',
                            artifact_pattern: task.artifact_pattern || '',
                            swaps: JSON.parse(JSON.stringify(task.file_swaps || []))
                        };
                    } else {
                        // 新建模式
                        this.editingId = null;
                        this.form = {
                            name: '',
                            command: '',
                            working_dir: this.form.working_dir || '.', 
                            min_gpus: 1,
                            max_gpus: 8,
                            retry_count: 1,
                            artifact_dir: '',
                            artifact_pattern: '',
                            swaps: []
                        };
                    }
                    this.showModal = true;
                },
                // [新增] 查看日志
                viewLog(taskId) {
                    this.currentLogTaskId = taskId;
                    this.showLogModal = true;
                    this.logContent = '';
                    this.fetchLog(taskId);
                },
                // [新增] 获取日志内容
                fetchLog(taskId) {
                    this.logLoading = true;
                    axios.get(`/api/tasks/${taskId}/log`).then(res => {
                        this.logContent = res.data.content;
                        // 自动滚动到底部
                        this.$nextTick(_ => {
                            if(this.$refs.logContainer) {
                                this.$refs.logContainer.scrollTop = this.$refs.logContainer.scrollHeight;
                            }
                        });
                    }).catch(err => {
                        this.logContent = "无法获取日志或日志文件不存在。\n" + (err.response?.data?.msg || err.message);
                    }).finally(_ => {
                        this.logLoading = false;
                    });
                },
                addSwap() {
                    this.form.swaps.push({source: '', target: ''});
                },
                submitTask() {
                    const fd = new FormData();
                    fd.append('name', this.form.name);
                    fd.append('command', this.form.command);
                    fd.append('working_dir', this.form.working_dir);
                    fd.append('min_gpus', this.form.min_gpus);
                    fd.append('max_gpus', this.form.max_gpus);
                    fd.append('retry_count', this.form.retry_count);
                    fd.append('artifact_dir', this.form.artifact_dir);
                    fd.append('artifact_pattern', this.form.artifact_pattern);
                    
                    const validSwaps = this.form.swaps.filter(s => s.source && s.target);
                    fd.append('swaps_json', JSON.stringify(validSwaps));

                    let url = '/api/tasks/create';
                    if (this.editingId) {
                        url = `/api/tasks/${this.editingId}/update`;
                    }

                    axios.post(url, fd).then(res => {
                        this.showModal = false;
                        this.loadData();
                        alert(this.editingId ? '任务已更新！' : '任务已加入队列！');
                    }).catch(err => alert('操作失败: ' + (err.response?.data?.msg || err.message)));
                },
                stopTask(id) {
                    if(!confirm('确定要停止该任务吗？')) return;
                    axios.post(`/api/tasks/${id}/stop`).then(this.loadData);
                },
                delTask(id) {
                    if(!confirm('确定要删除记录吗？')) return;
                    axios.delete(`/api/tasks/${id}`).then(this.loadData);
                },
                copyTask(id) {
                    axios.post(`/api/tasks/${id}/copy`).then(_ => {
                        this.loadData();
                        alert('任务已复制并暂停，请点击开始以加入队列。');
                    });
                },
                retryTask(id) {
                    axios.post(`/api/tasks/${id}/retry`).then(_ => {
                        this.loadData();
                        alert('任务已重新加入队列。');
                    });
                },
                startTask(id) {
                    axios.post(`/api/tasks/${id}/start`).then(_ => {
                        this.loadData();
                    });
                },
                formatTime(t) {
                    if(!t) return '-';
                    return new Date(t).toLocaleString('zh-CN', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'});
                }
            },
            mounted() {
                console.log("AutoTrainer Frontend Mounted.");
                this.loadData();
                setInterval(this.loadData, 3000); 
            }
        });
    </script>
</body>
</html>
"""

# ==============================================================================
# 1. 配置区域 (Configuration)
# ==============================================================================

# 数据库配置
SQLALCHEMY_DATABASE_URL = "sqlite:///./autotrainer_tasks.db"

# Eminder 配置
EMINDER_API_URL = "http://0.0.0.0:8421/api/send-now"
RECEIVER_EMAIL = "892640097@qq.com"
TEMPLATE_TYPE = "training_report"

# GPU 配置
GPU_MEMORY_THRESHOLD = 20000  # MB, 低于此值视为显卡空闲
GPU_CHECK_INTERVAL = 5       # 秒, 轮询间隔

# 日志配置
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("autotrainer_system.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("AutoTrainer")

# ==============================================================================
# 2. 数据库模型 (Database Models)
# ==============================================================================
Base = declarative_base()

# [修改点] 增加了 PAUSED 状态
class TaskStatus(str, Enum):
    PENDING = "pending"   # 排队中
    RUNNING = "running"   # 运行中
    COMPLETED = "completed" # 完成
    FAILED = "failed"     # 失败
    STOPPED = "stopped"   # 人工停止
    PAUSED = "paused"     # 暂停 (等待手动开始)

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    command = Column(Text)            # 启动命令
    working_dir = Column(String)      # 工作目录
    
    # 配置
    file_swaps = Column(JSON)         # [{"source": "...", "target": "..."}, ...]
    gpu_config = Column(JSON)         # {"min_gpus": 1, "max_gpus": 8}
    artifact_dir = Column(String, nullable=True)     # [新增] 产物目录
    artifact_pattern = Column(String, nullable=True) # [新增] 产物匹配模式
    
    # 状态
    status = Column(String, default=TaskStatus.PENDING)
    pid = Column(Integer, nullable=True)
    error_msg = Column(Text, nullable=True) # 记录最后的错误信息
    
    # 统计与重试
    created_at = Column(DateTime, default=datetime.datetime.now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    exit_code = Column(Integer, nullable=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=1)
    
    # 日志文件路径
    log_file_path = Column(String, nullable=True)

# 数据库初始化
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ==============================================================================
# 3. 辅助工具类 (Utils)
# ==============================================================================

class GPUMonitor:
    """负责显卡检测，支持多线程安全的NVML调用"""
    _lock = threading.Lock()

    @staticmethod
    def get_free_gpus(threshold_mb=GPU_MEMORY_THRESHOLD) -> List[int]:
        with GPUMonitor._lock:
            try:
                pynvml.nvmlInit()
                device_count = pynvml.nvmlDeviceGetCount()
                free_indices = []
                
                for i in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    used_mb = mem_info.used / 1024 / 1024
                    
                    # 只要小于阈值，认为是可以抢占/使用的
                    if used_mb < threshold_mb:
                        free_indices.append(i)
                
                return free_indices
            except Exception as e:
                return []
            finally:
                try:
                    pynvml.nvmlShutdown()
                except:
                    pass

class FileManager:
    """负责文件原子替换与回滚，确保环境纯净"""
    @staticmethod
    def apply_swaps(swap_list: List[Dict]) -> Dict[str, Optional[str]]:
        backups = {}
        try:
            for swap in swap_list:
                src = os.path.abspath(swap['source'])
                dst = os.path.abspath(swap['target'])
                
                if not os.path.exists(src):
                    raise FileNotFoundError(f"源文件未找到: {src}")
                
                if os.path.exists(dst):
                    timestamp = int(time.time() * 1000)
                    backup_path = f"{dst}.autotrainer_bak_{timestamp}"
                    shutil.copy2(dst, backup_path)
                    backups[dst] = backup_path
                else:
                    backups[dst] = None 
                
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                logger.info(f"Swapped: {src} -> {dst}")
                
        except Exception as e:
            logger.error(f"Swap failed: {e}. Rolling back...")
            FileManager.restore_swaps(backups)
            raise e
            
        return backups

    @staticmethod
    def restore_swaps(backups: Dict[str, Optional[str]]):
        for target, backup in backups.items():
            try:
                if backup and os.path.exists(backup):
                    if os.path.exists(target):
                        os.remove(target)
                    shutil.move(backup, target)
                    logger.info(f"Restored: {target}")
                elif backup is None and os.path.exists(target):
                    os.remove(target)
                    logger.info(f"Cleaned up: {target}")
            except Exception as e:
                logger.error(f"Failed to restore {target}: {e}")

class LogCleaner:
    @staticmethod
    def is_junk_line(line: str) -> bool:
        line_s = line.strip()
        if not line_s: return True
        if ('%|' in line_s or '|' in line_s) and ('it/s' in line_s or 's/it' in line_s):
            return True
        if "Detected call of `lr_scheduler.step()`" in line_s:
            return True
        return False
    
class ArtifactCollector:
    """负责在任务结束后扫描并收集文件"""
    @staticmethod
    def collect(directory: str, pattern: str) -> List[str]:
        if not directory or not os.path.exists(directory):
            return []
        
        # 支持递归搜索 pattern (e.g., **/*.jpg)
        search_path = os.path.join(directory, pattern)
        files = glob.glob(search_path, recursive=False)
        
        # 按修改时间排序，取最新的 5 个，防止附件过多
        files.sort(key=os.path.getmtime, reverse=True)
        return files[:5]

class EminderClient:
    @staticmethod
    def send_report(subject: str, content: str, attachments: List[str] = None):
        """
        发送邮件报告。
        [Requirement 1 Refinement]: 该方法内部已经处理了异常，
        但调用方仍建议使用 try-except 包裹，以应对不可预见的错误。
        """
        template_data = {
            "run_name": subject,
            "dataset": "AutoTrainer",
            "status": subject,
            "raw_log": content[-2000:] if len(content) > 2000 else content
        }
        
        payload = {
            "receiver_email": RECEIVER_EMAIL,
            "template_type": TEMPLATE_TYPE,
            "template_data_str": json.dumps(template_data),
            "custom_subject": subject
        }
        
        files = []
        opened_files = [] 
        
        if attachments:
            for path in attachments:
                if path and os.path.exists(path):
                    try:
                        f = open(path, 'rb')
                        opened_files.append(f)
                        files.append(('attachments', (os.path.basename(path), f)))
                    except Exception as e:
                        logger.error(f"Cannot attach file {path}: {e}")

        try:
            logger.info(f"Sending email to Eminder: {subject}")
            response = requests.post(EMINDER_API_URL, data=payload, files=files, timeout=30)
            if response.status_code == 200:
                logger.info("Email sent successfully.")
            else:
                logger.error(f"Eminder returned error: {response.status_code} {response.text}")
        except Exception as e:
            logger.error(f"Failed to connect to Eminder: {e}")
        finally:
            for f in opened_files:
                try: f.close() 
                except: pass

# ==============================================================================
# 4. 核心调度 Worker
# ==============================================================================
class TrainingWorker:
    def __init__(self):
        self.is_running = True
        self.current_proc = None
        self.current_task_id = None
        self._recover_state()
        
    def _recover_state(self):
        # 启动时将上次异常中断的任务标记为 Failed
        db = SessionLocal()
        try:
            stale = db.query(Task).filter(Task.status == TaskStatus.RUNNING).all()
            for t in stale:
                t.status = TaskStatus.FAILED
                t.error_msg = "System restart interrupted task."
            db.commit()
        finally:
            db.close()

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        
    def _loop(self):
        logger.info("AutoTrainer Worker Loop Started.")
        while self.is_running:
            try:
                self._check_queue()
            except Exception as e:
                logger.error(f"Worker Loop Error: {e}")
                traceback.print_exc()
                time.sleep(5)
            time.sleep(GPU_CHECK_INTERVAL)

    def _check_queue(self):
        db = SessionLocal()
        try:
            # [修改点] 显式忽略 PAUSED 状态的任务，只获取 PENDING
            task = db.query(Task).filter(Task.status == TaskStatus.PENDING).order_by(Task.created_at).first()
            if not task: return

            req_min = int(task.gpu_config.get("min_gpus", 1))
            req_max = int(task.gpu_config.get("max_gpus", 1))
            
            free_gpus = GPUMonitor.get_free_gpus()
            
            if len(free_gpus) >= req_min:
                use_gpus = free_gpus[:min(len(free_gpus), req_max)]
                self._execute_task(task.id, use_gpus, db)
        finally:
            db.close()

    def _execute_task(self, task_id, gpu_indices, db_session):
        # [修改点] 修复 LegacyAPIWarning: .query(Task).get() -> .get(Task, id)
        task = db_session.get(Task, task_id)
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.datetime.now()
        self.current_task_id = task.id
        
        cuda_str = ",".join(map(str, gpu_indices))
        log_path = os.path.abspath(f"logs/task_{task.id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        task.log_file_path = log_path
        
        db_session.commit()
        
        logger.info(f"Start Task {task.id} '{task.name}': GPUs {cuda_str}")
        
        # 准备环境 (核心修改：注入任务名)
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = cuda_str
        env["AUTOTRAINER_RUNNING"] = "true" 
        env["AUTOTRAINER_TASK_NAME"] = str(task.name)
        env["AUTOTRAINER_TASK_ID"] = str(task.id)
        
        # [关键逻辑] Windows 命令预处理
        # 解决 Windows cmd "conda activate && python" 执行完 activate 直接退出的 bug
        cmd_to_run = task.command
        if sys.platform == "win32":
            # Windows 修复
            if "conda activate" in cmd_to_run and "call conda activate" not in cmd_to_run:
                logger.info("Detect Windows conda activate: Auto-prepending 'call' to fix batch exit issue.")
                cmd_to_run = cmd_to_run.replace("conda activate", "call conda activate")
        else:
            # [关键修复] Linux 修复: "Run 'conda init' before 'conda activate'"
            # subprocess 在 Linux 上以非交互模式启动 bash，不会自动加载 .bashrc。
            # 解决方案：手动执行 conda 的 shell hook 脚本来注册 'conda' 函数。
            if "conda activate" in cmd_to_run:
                logger.info("Detect Linux conda activate: Prepending conda shell hook to fix 'conda init' error.")
                # 显式加载 Conda Shell Hook
                cmd_to_run = f"eval \"$(conda shell.bash hook)\" && {cmd_to_run}"
        
        shell_executable = "/bin/bash" if sys.platform != "win32" and os.path.exists("/bin/bash") else None
        
        log_buffer_system_err = [] # [修改] 仅用于捕获系统级异常，如 spawn 失败
        exit_code = -1
        oom_detected = False
        
        # [关键修复] 用于存储真正输出到邮件的日志缓冲区 (stdout)
        log_buffer_for_email = [] 
        
        # [关键修复] 提前初始化 backups，防止 finally 中 UnboundLocalError
        backups = {} 
        
        # [关键修复] 附件列表初始化
        attachments = [log_path]

        try:
            if task.file_swaps:
                backups = FileManager.apply_swaps(task.file_swaps)
            
            # [修改点] 需求①：确保 Eminder 失败不影响任务启动。
            # 虽然 EminderClient 内部有 catch，但这里再次包裹，防止 send_report 抛出未捕获的异常（如参数错误）中断流程。
            try:
                EminderClient.send_report(
                    f"任务开始: {task.name}",
                    f"Task ID: {task.id}\nGPUs: {cuda_str}\nWorkDir: {task.working_dir}\nCommand:\n{cmd_to_run}"
                )
            except Exception as eminder_e:
                logger.error(f"Eminder Start-Notification Failed (Ignored for robustness): {eminder_e}")
            
            env = os.environ.copy()
            env["CUDA_VISIBLE_DEVICES"] = cuda_str
            env["NCCL_P2P_DISABLE"] = "1" 
            env["NCCL_IB_DISABLE"] = "1"
            env["MASTER_ADDR"] = "localhost" 
            
            working_dir = task.working_dir if task.working_dir else "."
            if not os.path.exists(working_dir):
                os.makedirs(working_dir, exist_ok=True)

            with open(log_path, "w", encoding='utf-8') as lf:
                # [新增] 显式在日志文件中记录实际运行的命令，同时添加到邮件 Buffer
                header_info = f"=== AutoTrainer Execution Started ===\nTimestamp: {datetime.datetime.now()}\nPlatform: {sys.platform}\nActual Command Executed:\n{cmd_to_run}\n=====================================\n\n"
                
                lf.write(header_info)
                lf.flush()
                log_buffer_for_email.append(header_info) # 同步到邮件正文
                
                self.current_proc = subprocess.Popen(
                    cmd_to_run,
                    shell=True,
                    cwd=task.working_dir if os.path.exists(task.working_dir or "") else ".",
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, # 将 stderr 合并到 stdout
                    text=True,
                    bufsize=1,
                    executable=shell_executable # 强行指定 bash
                )
                
                task.pid = self.current_proc.pid
                db_session.commit()
                
                for line in self.current_proc.stdout:
                    lf.write(line)
                    lf.flush() # [关键修复] 确保每行日志都写入磁盘，防止日志为空
                    
                    lower_line = line.lower()
                    if "out of memory" in lower_line or "cuda out of memory" in lower_line:
                        oom_detected = True
                        
                    if not LogCleaner.is_junk_line(line):
                        log_buffer_for_email.append(line)
                        if len(log_buffer_for_email) > 300:
                            log_buffer_for_email.pop(0)
                
                self.current_proc.wait()
                exit_code = self.current_proc.returncode

        except Exception as e:
            logger.error(f"Execution Error: {e}")
            log_buffer_system_err.append(f"\n\nSystem Error: {str(e)}")
            exit_code = -999
        finally:
            FileManager.restore_swaps(backups)
            self.current_proc = None
            
            task.finished_at = datetime.datetime.now()
            task.exit_code = exit_code
            
            # === 产物扫描逻辑 ===
            if task.artifact_dir:
                found_files = ArtifactCollector.collect(task.artifact_dir, task.artifact_pattern)
                if found_files:
                    logger.info(f"Found artifacts: {found_files}")
                    # [关键修复] 添加找到的文件到附件列表
                    attachments.extend(found_files)
            
            # [关键修复] 合并stdout日志和系统错误日志
            final_log_lines = log_buffer_for_email + log_buffer_system_err
            log_str = "".join(final_log_lines[-300:])
            
            # [修改点] 需求①：确保所有状态报告的 Eminder 调用都包裹在 try-except 中
            try:
                if exit_code == 0:
                    task.status = TaskStatus.COMPLETED
                    task.retry_count = 0
                    db_session.commit()
                    EminderClient.send_report(
                        f"任务成功: {task.name}",
                        f"Duration: {task.finished_at - task.started_at}\n\nLogs Tail:\n{log_str}",
                        attachments=attachments
                    )
                elif task.status == TaskStatus.STOPPED:
                    EminderClient.send_report(
                        f"任务被手动停止: {task.name}",
                        f"User interrupted task.\n\nLogs Tail:\n{log_str}",
                        attachments=attachments
                    )
                else:
                    can_retry = task.retry_count < task.max_retries
                    
                    if can_retry:
                        task.retry_count += 1
                        task.status = TaskStatus.PENDING 
                        task.pid = None
                        EminderClient.send_report(
                            f"任务出现错误，正在重试 ({task.retry_count}/{task.max_retries}): {task.name}",
                            f"Detected Error/OOM. Re-queueing task.\nExit Code: {exit_code}\nOOM Detected: {oom_detected}\n\nLogs Tail:\n{log_str}",
                            attachments=attachments
                        )
                        logger.warning(f"Task {task.id} failed (code {exit_code}). Retrying {task.retry_count}/{task.max_retries}")
                    else:
                        task.status = TaskStatus.FAILED
                        EminderClient.send_report(
                            f"任务最终失败 (次数: {task.retry_count}): {task.name}",
                            f"Max retries reached.\nExit Code: {exit_code}\nOOM: {oom_detected}\n\nLogs Tail:\n{log_str}",
                            attachments=attachments
                        )
            except Exception as eminder_end_e:
                logger.error(f"Eminder End-Notification Failed (Ignored): {eminder_end_e}")
            
            db_session.commit()

    def stop_current_task(self):
        if self.current_proc:
            try:
                if sys.platform == "win32":
                    subprocess.call(['taskkill', '/F', '/T', '/PID', str(self.current_proc.pid)])
                else:
                    pgid = os.getpgid(self.current_proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                logger.info(f"Killed process {self.current_proc.pid}")
            except Exception as e:
                logger.error(f"Failed to kill process: {e}")

worker = TrainingWorker()
worker.start()

# ==============================================================================
# 5. API 接口 (FastAPI)
# ==============================================================================
app = FastAPI(title="AutoTrainer Pro")

# [关键步骤] 每次启动都强制重写 HTML 模板，确保修复生效
def check_and_init_resources():
    os.makedirs("templates", exist_ok=True)
    template_path = os.path.join("templates", "dashboard.html")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(HTML_TEMPLATE_CONTENT)
    logger.info("Dashboard template initialized/updated.")

check_and_init_resources()

templates = Jinja2Templates(directory="templates")

# Jinja2 配置
templates.env.variable_start_string = '(('
templates.env.variable_end_string = '))'

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/api/stats")
def get_dashboard_stats():
    db = SessionLocal()
    try:
        now = datetime.datetime.now()
        month_ago = now - datetime.timedelta(days=30)
        tasks_30d = db.query(Task).filter(Task.created_at >= month_ago).all()
        
        stats = {
            "total_30d": len(tasks_30d),
            "success_30d": sum(1 for t in tasks_30d if t.status == TaskStatus.COMPLETED),
            "failed_30d": sum(1 for t in tasks_30d if t.status == TaskStatus.FAILED),
            "pending": db.query(Task).filter(Task.status == TaskStatus.PENDING).count(),
            "running": db.query(Task).filter(Task.status == TaskStatus.RUNNING).count(),
        }
        
        gpu_data = []
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                util = pynvml.nvmlDeviceGetUtilizationRates(h)
                name = pynvml.nvmlDeviceGetName(h)
                if isinstance(name, bytes): name = name.decode('utf-8')
                
                used_mb = mem.used / 1024**2
                is_free = used_mb < GPU_MEMORY_THRESHOLD
                
                gpu_data.append({
                    "id": i,
                    "name": name,
                    "mem_used": int(used_mb),
                    "mem_total": int(mem.total / 1024**2),
                    "util": util.gpu,
                    "is_free": is_free
                })
        except Exception:
            pass
        finally:
            try: pynvml.nvmlShutdown()
            except: pass

        tasks = db.query(Task).order_by(
            desc(Task.status == TaskStatus.RUNNING),
            desc(Task.status == TaskStatus.PENDING),
            desc(Task.created_at)
        ).limit(50).all()
        
        return {"stats": stats, "gpus": gpu_data, "tasks": tasks}
    finally:
        db.close()

# [新增 API] 获取任务日志内容
# 需求②：为 dashboard 提供日志数据
@app.get("/api/tasks/{tid}/log")
def get_task_log(tid: int):
    db = SessionLocal()
    try:
        task = db.get(Task, tid)
        if not task:
            return JSONResponse(status_code=404, content={"msg": "Task not found"})
        
        log_path = task.log_file_path
        
        if not log_path or not os.path.exists(log_path):
            return JSONResponse(status_code=404, content={"msg": "Log file not created yet or missing"})
            
        # 安全起见，只读取最后 1MB 数据，避免日志过大撑爆浏览器
        max_bytes = 1024 * 1024  # 1MB
        file_size = os.path.getsize(log_path)
        
        try:
            with open(log_path, 'rb') as f:
                if file_size > max_bytes:
                    f.seek(file_size - max_bytes)
                    content_bytes = f.read(max_bytes)
                    # 处理截断的 utf-8 字符
                    content = content_bytes.decode('utf-8', errors='ignore')
                    content = "[Warning: Log too large, showing last 1MB only]\n" + content
                else:
                    content = f.read().decode('utf-8', errors='ignore')
            return {"content": content}
        except Exception as e:
            return JSONResponse(status_code=500, content={"msg": f"Error reading log: {str(e)}"})
    finally:
        db.close()

@app.post("/api/tasks/create")
async def create_task(
    name: str = Form(...),
    command: str = Form(...),
    working_dir: str = Form(...),
    min_gpus: int = Form(1),
    max_gpus: int = Form(8),
    retry_count: int = Form(1),
    artifact_dir: str = Form(""),
    artifact_pattern: str = Form(""),
    swaps_json: str = Form("[]")
):
    try:
        swaps = json.loads(swaps_json)
    except:
        return JSONResponse(status_code=400, content={"msg": "Invalid JSON swaps"})
    
    db = SessionLocal()
    try:
        new_task = Task(
            name=name,
            command=command,
            working_dir=working_dir,
            file_swaps=swaps,
            gpu_config={"min_gpus": min_gpus, "max_gpus": max_gpus},
            max_retries=retry_count,
            artifact_dir=artifact_dir,
            artifact_pattern=artifact_pattern,
            status=TaskStatus.PENDING
        )
        db.add(new_task)
        db.commit()
    finally:
        db.close()
    return {"msg": "Task created"}

# [新增 API] 编辑更新任务
@app.post("/api/tasks/{tid}/update")
async def update_task(
    tid: int,
    name: str = Form(...),
    command: str = Form(...),
    working_dir: str = Form(...),
    min_gpus: int = Form(1),
    max_gpus: int = Form(8),
    retry_count: int = Form(1),
    artifact_dir: str = Form(""),
    artifact_pattern: str = Form(""),
    swaps_json: str = Form("[]")
):
    try:
        swaps = json.loads(swaps_json)
    except:
        return JSONResponse(status_code=400, content={"msg": "Invalid JSON swaps"})
        
    db = SessionLocal()
    try:
        # [修改点] 修复 LegacyAPIWarning
        task = db.get(Task, tid)
        if not task:
            return JSONResponse(status_code=404, content={"msg": "Not found"})
        
        if task.status == TaskStatus.RUNNING:
            return JSONResponse(status_code=400, content={"msg": "Cannot edit running task"})

        # 更新基本字段
        task.name = name
        task.command = command
        task.working_dir = working_dir
        task.file_swaps = swaps
        task.gpu_config = {"min_gpus": min_gpus, "max_gpus": max_gpus}
        task.max_retries = retry_count
        task.artifact_dir = artifact_dir
        task.artifact_pattern = artifact_pattern
        
        # 如果是已完成/失败/停止的任务，编辑后重置为 Pending
        if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED]:
            task.status = TaskStatus.PENDING
            task.started_at = None
            task.finished_at = None
            task.exit_code = None
            task.log_file_path = None
            task.retry_count = 0
            task.error_msg = None
            
        db.commit()
    finally:
        db.close()
    return {"msg": "Task updated"}

# [新增 API] 复制任务
@app.post("/api/tasks/{tid}/copy")
def copy_task(tid: int):
    db = SessionLocal()
    try:
        # [修改点] 修复 LegacyAPIWarning
        src_task = db.get(Task, tid)
        if not src_task:
            return JSONResponse(status_code=404, content={"msg": "Not found"})
        
        new_task = Task(
            name=f"{src_task.name} (Copy)",
            command=src_task.command,
            working_dir=src_task.working_dir,
            file_swaps=src_task.file_swaps,
            gpu_config=src_task.gpu_config,
            max_retries=src_task.max_retries,
            artifact_dir=src_task.artifact_dir,
            artifact_pattern=src_task.artifact_pattern,
            # 复制后设为暂停
            status=TaskStatus.PAUSED
        )
        db.add(new_task)
        db.commit()
    finally:
        db.close()
    return {"msg": "Copied"}

# [新增 API] 重试任务
@app.post("/api/tasks/{tid}/retry")
def retry_task(tid: int):
    db = SessionLocal()
    try:
        # [修改点] 修复 LegacyAPIWarning
        task = db.get(Task, tid)
        if not task:
            return JSONResponse(status_code=404, content={"msg": "Not found"})
        
        if task.status not in [TaskStatus.FAILED, TaskStatus.COMPLETED, TaskStatus.STOPPED]:
            return JSONResponse(status_code=400, content={"msg": "Can only retry finished tasks"})
        
        task.status = TaskStatus.PENDING
        task.started_at = None
        task.finished_at = None
        task.exit_code = None
        task.retry_count = 0
        task.log_file_path = None
        task.error_msg = None
        
        db.commit()
    finally:
        db.close()
    return {"msg": "Retrying"}

# [新增 API] 开始任务 (从暂停恢复)
@app.post("/api/tasks/{tid}/start")
def start_task(tid: int):
    db = SessionLocal()
    try:
        # [修改点] 修复 LegacyAPIWarning
        task = db.get(Task, tid)
        if not task:
            return JSONResponse(status_code=404, content={"msg": "Not found"})
        
        if task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.PENDING
            db.commit()
    finally:
        db.close()
    return {"msg": "Started"}

@app.post("/api/tasks/{tid}/stop")
def stop_task(tid: int):
    db = SessionLocal()
    try:
        # [修改点] 修复 LegacyAPIWarning
        task = db.get(Task, tid)
        if not task:
            return JSONResponse(status_code=404, content={"msg": "Not found"})
        
        if task.status == TaskStatus.RUNNING:
            if worker.current_task_id == tid:
                worker.stop_current_task()
            task.status = TaskStatus.STOPPED
            task.finished_at = datetime.datetime.now()
            task.error_msg = "Manually stopped by user"
        elif task.status == TaskStatus.PENDING:
            task.status = TaskStatus.STOPPED
        
        db.commit()
    finally:
        db.close()
    return {"msg": "Stopped"}

@app.delete("/api/tasks/{tid}")
def delete_task(tid: int):
    db = SessionLocal()
    try:
        # [修改点] 修复 LegacyAPIWarning
        task = db.get(Task, tid)
        if task:
            if task.status == TaskStatus.RUNNING:
                if worker.current_task_id == tid:
                    worker.stop_current_task()
            db.delete(task)
            db.commit()
    finally:
        db.close()
    return {"msg": "Deleted"}

if __name__ == "__main__":
    import uvicorn
    # 0.0.0.0 允许局域网访问
    uvicorn.run(app, host="0.0.0.0", port=8080)