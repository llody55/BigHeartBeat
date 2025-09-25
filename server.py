import threading
import os
import logging
import re
import time
from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from prometheus_client import Gauge, generate_latest
from flask import Response
import requests
from logging.config import dictConfig

# 从环境变量获取配置，设置默认值
# 日志配置
LOG_LEVEL = os.getenv('LOG_LEVEL', 'WARNING').upper()

# VictoriaMetrics 的导入地址
VICTORIA_METRICS_PROTOCOL = os.getenv('VICTORIA_METRICS_PROTOCOL', 'http')
VICTORIA_METRICS_HOST = os.getenv('VICTORIA_METRICS_HOST', '192.168.1.227:31689')
VICTORIA_METRICS_PATH = os.getenv('VICTORIA_METRICS_PATH', '/api/v1/import/prometheus')
VICTORIA_METRICS_URL = f"{VICTORIA_METRICS_PROTOCOL}://{VICTORIA_METRICS_HOST}{VICTORIA_METRICS_PATH}"

# 主机超时阈值（30秒）
TIMEOUT_THRESHOLD_SECONDS = int(os.getenv('TIMEOUT_THRESHOLD_SECONDS', '30'))
TIMEOUT_THRESHOLD = timedelta(seconds=TIMEOUT_THRESHOLD_SECONDS)

# 状态检查间隔（秒），默认20秒
CHECK_INTERVAL_SECONDS = int(os.getenv('CHECK_INTERVAL_SECONDS', '20'))

# 主循环睡眠间隔（秒），默认1秒
MAIN_LOOP_SLEEP_SECONDS = int(os.getenv('MAIN_LOOP_SLEEP_SECONDS', '1'))

# Web应用端口，默认5000
WEB_APP_PORT = int(os.getenv('WEB_APP_PORT', '5000'))

# API应用端口，默认5001
API_APP_PORT = int(os.getenv('API_APP_PORT', '5001'))

VALID_LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
if LOG_LEVEL not in VALID_LOG_LEVELS:
    LOG_LEVEL = 'INFO'

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
            'level': LOG_LEVEL,
            'stream': 'ext://sys.stdout'
        },
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': True
        },
        'werkzeug': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False
        },
        'flask': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False
        },
        'apscheduler': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False
        }
    }
}
dictConfig(LOGGING_CONFIG)

# 创建 Web 和 API 应用
web_app = Flask(__name__, template_folder='templates')
api_app = Flask(__name__)

# # 配置 SQLite 数据库
# web_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///registered_hosts.db'
# web_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# api_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///registered_hosts.db'
# api_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 配置 SQLite 数据库 - 添加连接池配置
web_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///registered_hosts.db'
web_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
web_app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 300,
    'pool_pre_ping': True
}
api_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///registered_hosts.db'
api_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
api_app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 300,
    'pool_pre_ping': True
}

db = SQLAlchemy()

# 初始化数据库
db.init_app(web_app)
db.init_app(api_app)

# 定义主机信息模型
class Host(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    host_id = db.Column(db.String(128), unique=True, nullable=False)
    hostname = db.Column(db.String(128), nullable=False)
    region = db.Column(db.String(64), nullable=True)
    ip = db.Column(db.String(15), nullable=False)
    public_ip = db.Column(db.String(15), nullable=False)
    os_version = db.Column(db.String(128), nullable=False)
    client_version = db.Column(db.String(20), nullable=True)
    os_details = db.Column(db.JSON, nullable=True)
    last_report_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(4), default='up', nullable=False)
    metrics = db.Column(db.JSON, nullable=True)  # 存储动态指标

# 创建数据库表
with web_app.app_context():
    db.create_all()

# Prometheus 指标：主机状态
HOST_STATUS = Gauge('host_status', 'Host status (1=up, 0=down)', ['host_id', 'hostname'])

# Web 应用路由
@web_app.route('/')
def index():
    return render_template('index.html')

@web_app.route('/list_hosts', methods=['GET'])
def list_hosts():
    hosts_with_status = [
        {
            'host_id': host.host_id,
            'region': host.region,
            'hostname': host.hostname,
            'ip': host.ip,
            'public_ip': host.public_ip,
            'os_version': host.os_version,
            'client_version': host.client_version,
            'last_report_time': host.last_report_time.isoformat(),
            'status': host.status
        }
        for host in Host.query.all()
    ]
    return jsonify({
        "code": 0,
        "msg": "",
        "count": len(hosts_with_status),
        "data": hosts_with_status
    })

@web_app.route('/delete_host', methods=['POST'])
def delete_host():
    data = request.json
    host_id = data.get('host_id')
    if not host_id:
        return jsonify({'status': 'error', 'message': 'host_id is required'}), 400
    host = Host.query.filter_by(host_id=host_id).first()
    if not host:
        return jsonify({'status': 'error', 'message': 'Host not found'}), 404
    if host.status != 'down':
        return jsonify({'status': 'error', 'message': 'Host is still up'}), 400
    db.session.delete(host)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Host deleted successfully'})

@web_app.route('/host_details/<host_id>', methods=['GET'])
def host_details(host_id):
    host = Host.query.filter_by(host_id=host_id).first()
    if not host:
        return jsonify({'status': 'error', 'message': 'Host not found'}), 404
    metrics = host.metrics or {
        'client_uptime': [{'value': [None, str(int((datetime.now() - host.last_report_time).total_seconds()))]}],
        'client_cpu_usage': [{'value': [None, 0]}],
        'client_memory_usage': [{'value': [None, 0]}],
        'client_process_count': [{'value': [None, 0]}]
    }
    return render_template('host_details.html', host=host, metrics=metrics)

@web_app.route('/host_details_json/<host_id>', methods=['GET'])
def host_details_json(host_id):
    host = Host.query.filter_by(host_id=host_id).first()
    if not host:
        return jsonify({'status': 'error', 'message': 'Host not found'}), 404
    metrics = host.metrics or {
        'client_uptime': [{'value': [None, str(int((datetime.now() - host.last_report_time).total_seconds()))]}],
        'client_cpu_usage': [{'value': [None, 0]}],
        'client_memory_usage': [{'value': [None, 0]}],
        'client_process_count': [{'value': [None, 0]}]
    }
    response = {
        'status': 'success',
        'host': {
            'host_id': host.host_id,
            'region': host.region,
            'hostname': host.hostname,
            'ip': host.ip,
            'public_ip': host.public_ip,
            'os_version': host.os_version,
            'client_version': host.client_version,
            'os_details': host.os_details or {},
            'last_report_time': host.last_report_time.isoformat(),
            'status': host.status,
            'metrics': metrics
        }
    }
    return jsonify(response)

# API 应用路由
@api_app.route('/register', methods=['POST'])
def register():
    data = request.json
    host_id = data.get('host_id')
    if not host_id:
        return jsonify({'status': 'error', 'message': 'host_id is required'}), 400
    host = Host.query.filter_by(host_id=host_id).first()
    if host:
        host.hostname = data['hostname']
        host.region = data.get('region', '')
        host.ip = data['ip']
        host.public_ip = data['public_ip']
        host.os_version = data['os_version']
        host.client_version = data.get('client_version', 'unknown')
        host.os_details = data.get('os_details', {})
        host.last_report_time = datetime.now()
        host.status = 'up'
    else:
        host = Host(
            host_id=host_id,
            hostname=data['hostname'],
            region=data.get('region', ''),
            ip=data['ip'],
            public_ip=data['public_ip'],
            os_version=data['os_version'],
            client_version=data.get('client_version', 'unknown'),
            os_details=data.get('os_details', {}),
            last_report_time=datetime.now(),
            status='up',
            metrics={}
        )
        db.session.add(host)
    db.session.commit()
    logging.info(f"Host registered: {host_id}")
    return jsonify({'status': 'registered', 'host': data})

@api_app.route('/report', methods=['POST'])
def report():
    try:
        data = request.data.decode('utf-8')
        host_id = request.headers.get('X-Hostid')
        logging.info(f"Received report with host_id: {host_id}, data size: {len(data)} bytes")
  
        # 解析 Prometheus 指标
        metrics = {}
        expected_metrics = ['client_uptime', 'client_cpu_usage', 'client_memory_usage', 'client_process_count']
        for line in data.split('\n'):
            if line and not line.startswith('#'):
                # 优化正则表达式，支持更灵活的格式
                match = re.match(r'(\w+)(?:\{([^}]*)\})?\s+([-]?[\d\.eE\+-]+)(?:\s+\d+)?$', line)
                if match:
                    metric_name, labels, value = match.groups()
                    logging.debug(f"Parsed line: metric_name={metric_name}, labels={labels}, value={value}")
                    if metric_name in expected_metrics:
                        try:
                            metrics[metric_name] = [{'value': [None, float(value)]}]
                            logging.debug(f"Stored metric {metric_name}: {metrics[metric_name]}")
                        except ValueError:
                            logging.warning(f"Invalid value for metric {metric_name}: {value}")
                    else:
                        logging.debug(f"Ignored metric: {metric_name} (not in expected_metrics)")
                else:
                    logging.warning(f"Failed to parse Prometheus line: {line}")

        # 检查是否所有预期指标都已解析
        for metric in expected_metrics:
            if metric not in metrics:
                logging.warning(f"Metric {metric} not found in report for host {host_id}, setting default")
                metrics[metric] = [{'value': [None, 0]}]

        # 更新数据库中的指标
        host = Host.query.filter_by(host_id=host_id).first()
        if host:
            host.last_report_time = datetime.now()
            host.status = 'up'
            host.metrics = metrics
            db.session.commit()
            logging.info(f"Updated host {host_id} in database with metrics: {metrics}")
        else:
            logging.info(f"Host with host_id {host_id} not found in database")
            return jsonify({'status': 'error', 'message': 'Host not registered'}), 400

        # 转发到 VictoriaMetrics
        headers = {'Content-Type': 'text/plain; charset=utf-8'}
        response = requests.post(VICTORIA_METRICS_URL, data=data.encode('utf-8'), headers=headers)
        response.raise_for_status()
        logging.info(f"Successfully forwarded to VictoriaMetrics. Status: {response.status_code}, Response: {response.text}")

        return jsonify({'status': 'success'})
    except Exception as e:
        logging.error(f"Error processing /report request: {e}")
        return jsonify({'status': 'failed', 'error': str(e)}), 500

@api_app.route('/metrics', methods=['GET'])
def metrics():
    for host in Host.query.all():
        HOST_STATUS.labels(host_id=host.host_id,region=host.region, hostname=host.hostname,ip=host.ip, public_ip=host.public_ip, os_version=host.os_version, client_version=host.client_version).set(1 if host.status == 'up' else 0)
    return Response(generate_latest(), mimetype='text/plain', content_type='text/plain; charset=utf-8')

# 定时检查主机状态
def check_host_status():
    current_time = datetime.now()
    with api_app.app_context():
        for host in Host.query.all():
            time_diff = current_time - host.last_report_time
            if time_diff > TIMEOUT_THRESHOLD and host.status != 'down':
                host.status = 'down'
                db.session.commit()
                logging.warning(f"Host {host.hostname} is down")

# 统一启动函数
def run_apps():
    web_thread = threading.Thread(target=web_app.run, kwargs={'host': '0.0.0.0', 'port': WEB_APP_PORT})
    api_thread = threading.Thread(target=api_app.run, kwargs={'host': '0.0.0.0', 'port': API_APP_PORT})
    web_thread.daemon = True
    api_thread.daemon = True
    web_thread.start()
    api_thread.start()
    return web_thread, api_thread

if __name__ == '__main__':
    import time
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_host_status, 'interval', seconds=CHECK_INTERVAL_SECONDS)
    scheduler.start()
    web_thread, api_thread = run_apps()
    try:
        while True:
            time.sleep(MAIN_LOOP_SLEEP_SECONDS)
    except KeyboardInterrupt:
        scheduler.shutdown()
        logging.info("Shutting down server")