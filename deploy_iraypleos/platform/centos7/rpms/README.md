# CentOS 7 Python 3.9 离线安装包

部署脚本按以下优先级尝试安装 Python 3.9：

## 1. SCL (推荐)

```bash
# 在线环境:
yum install -y centos-release-scl
yum install -y rh-python39

# 离线: 下载以下 RPM 放入此目录
#   rh-python39-*.rpm
#   rh-python39-python3-*.rpm
#   rh-python39-python3-pip-*.rpm
```

## 2. 预编译包

将编译好的 Python 3.9 打包为 `python39_build.tar.gz` 放入此目录：

```bash
# 在有 Python 3.9 的开发机上:
cd /usr/local
tar czf python39_build.tar.gz python3/
# 将 python39_build.tar.gz 放到 platform/centos7/rpms/
```

要求：
- 静态编译或目标机器 glibc 版本一致
- 解压后 `/usr/local/python3/bin/python3` 可用
- 包含 pip 和 venv 模块

## 3. 源码编译

将 Python 源码包和编译依赖 RPM 放入此目录：

```bash
# 下载 Python 3.9 源码 (约 20MB):
wget https://www.python.org/ftp/python/3.9.20/Python-3.9.20.tar.xz

# 下载编译依赖 RPM:
#   openssl-devel, bzip2-devel, libffi-devel,
#   zlib-devel, readline-devel, sqlite-devel
```

编译过程约 5-15 分钟，需要 gcc 和 make。
