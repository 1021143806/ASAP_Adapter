# ASAP Adapter 生产环境升级手册

## 概述

本文档适用于对生产服务器 **10.68.2.10** 上的 ASAP Adapter 进行在线升级的操作指引。

## 环境信息

| 项目 | 值 |
|------|-----|
| 服务器 IP | 10.68.2.10 |
| 服务端口 | 5012 |
| 部署方式 | Supervisor (`asap_adapter`) |
| 项目路径 | `/main/app/ASAP_Adapter` |
| 配置文件 | `config/env.toml` (base) + `config/overrides.json` (runtime overrides) |
| 日志路径 | `logs/asap.log` |

## 升级流程

### 1. 制作升级包

从开发环境的项目根目录打包修改过的文件：

```bash
cd /main/app/github/ASAP_Adapter

# 创建版本说明
cat > version.json << 'EOF'
{"title":"v1.x.x 版本名称","changes":["改动1: xxx","改动2: xxx"]}
EOF

# 打包（只包含修改过的文件）
zip -r upgrade_v1.x.x.zip \
  asap_adapter/router.py \
  asap_adapter/static/index.html \
  version.json

rm version.json
```

**注意事项：**
- 不要打包 `config/env.toml`、`venv/`、`logs/`、`backup/`、`.git/` 等排除项
- `config/overrides.json` 是运行时持久化文件，不会因升级丢失
- 如新增依赖，需同时更新 `deploy_iraypleos/vendor_packages3.9/` 下的离线包

### 2. 上传升级

#### 方式一：WebUI（推荐）
浏览器打开 `http://10.68.2.10:5012/upgrade`，拖拽 ZIP 包上传。

#### 方式二：curl 命令行
```bash
curl -X POST http://10.68.2.10:5012/api/asap/upgrade/upload \
  -F "file=@upgrade_v1.x.x.zip" \
  -F "remark=版本描述信息"
```

### 3. 验证升级

```bash
# 等待服务重启（约 5 秒）
sleep 5

# 健康检查（返回纯文本 "1000"）
curl http://10.68.2.10:5012/actuator/health

# 检查 WebUI 是否正常加载
curl -s http://10.68.2.10:5012/ | grep -oP 'view-door|view-zone|view-config'

# 检查 API 是否正常
curl http://10.68.2.10:5012/api/asap/status
```

### 4. 回滚

如升级出现问题，可通过 WebUI 或 API 回滚：

```bash
# 查看升级记录
curl http://10.68.2.10:5012/api/asap/upgrade/records

# 回滚到指定备份
curl -X POST http://10.68.2.10:5012/api/asap/upgrade/rollback/{backup_name}
```

## 常见操作

### 配置文件编辑

**可视化编辑**（运行时生效，自动持久化到 overrides.json）：
- RCS 配置: WebUI → 配置管理 → 可视化编辑 → RCS 配置
- AB 门配置: WebUI → 配置管理 → 可视化编辑 → AB 自动门配置
- 区域管控配置: WebUI → 区域管控 → 区域管控对接配置

**直接文件编辑**（需重启生效）：
```bash
# 通过 API 读取
curl http://10.68.2.10:5012/api/asap/config/file

# 通过 API 写入（自动备份原文件，验证 TOML 格式）
curl -X POST http://10.68.2.10:5012/api/asap/config/file \
  -H "Content-Type: application/json" \
  -d '{"content":"# your toml content here..."}'
```

### 查看状态

```bash
# 服务状态
supervisorctl status asap_adapter

# 实时日志
tail -f /main/app/ASAP_Adapter/logs/asap.log

# WebUI 健康
curl http://10.68.2.10:5012/actuator/health

# 升级记录
curl http://10.68.2.10:5012/api/asap/upgrade/records
```

## 升级历史

| 版本 | 日期 | 内容 |
|------|------|------|
| v1.1.0 | 2026-06-13 | 仪表盘模块化重构: 风淋门/区域管控/配置管理 三个独立标签页，新增配置文件直接编辑功能 |
| v1.0.0 | - | 初始版本 |

## 注意事项

1. **config/env.toml 不会随升级覆盖**，系统使用 `config/overrides.json` 持久化运行时配置
2. 升级后服务会自动重启（约 3 秒），期间请求会短暂中断
3. 如升级包中包含 `version.json`，升级记录会显示版本标题和变更列表
4. 生产环境为离线环境，新增 Python 依赖需预先下载到 `deploy_iraypleos/vendor_packages3.9/`
5. 如果 WebUI 不能正常访问，先检查服务是否已重启完成
