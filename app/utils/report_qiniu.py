"""Store a report PNG in Qiniu Kodo and verify its public download."""

import base64
import hmac
import json
import re
import secrets
import socket
import time
from http.client import HTTPException, IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


REGIONS = {
    "华东-浙江": "up-z0.qiniup.com",
    "华东-浙江2": "up-cn-east-2.qiniup.com",
    "华北-河北": "up-z1.qiniup.com",
    "华南-广东": "up-z2.qiniup.com",
    "北美-洛杉矶": "up-na0.qiniup.com",
    "亚太-新加坡": "up-as0.qiniup.com",
    "亚太-河内": "up-ap-southeast-2.qiniup.com",
    "亚太-胡志明": "up-ap-southeast-3.qiniup.com",
}
SECRET_FIELDS = ("access_key", "secret_key")
FIELDS = ("region", "bucket", "base_url", "prefix", *SECRET_FIELDS)


def clean_config(value, previous=None):
    value = value if isinstance(value, dict) else {}
    config = dict(previous or {})
    for field in FIELDS:
        if field not in value:
            continue
        item = value[field]
        item = item.strip()[:512] if isinstance(item, str) else ""
        if item or field not in SECRET_FIELDS:
            config[field] = item
    for field in SECRET_FIELDS:
        if value.get("clear_" + field) is True:
            config[field] = ""
    return config


def public_config(config):
    public = {field: config.get(field, "") for field in FIELDS if field not in SECRET_FIELDS}
    public.update({field + "_set": bool(config.get(field)) for field in SECRET_FIELDS})
    return public


def validate(config):
    if config.get("region") not in REGIONS:
        raise ValueError("七牛配置：请选择存储区域")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", config.get("bucket", "")):
        raise ValueError("七牛配置：请填写有效的空间名称")
    try:
        url = urlsplit(config.get("base_url", ""))
        valid = (url.scheme in ("http", "https") and url.hostname and "." in url.hostname
                 and not url.username and not url.password and url.port is None
                 and not url.query and not url.fragment and url.path in ("", "/")
                 and re.fullmatch(r"[A-Za-z0-9.-]+", url.netloc))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("七牛配置：下载域名应为 http(s)://域名，不含目录")
    prefix = config.get("prefix", "").rstrip("/")
    if prefix and any(not part or part in (".", "..") or not re.fullmatch(r"[\w.-]+", part)
                      for part in prefix.split("/")):
        raise ValueError("七牛配置：图片目录请填写相对目录，例如 reports/")
    for field, label in zip(SECRET_FIELDS, ("AccessKey", "SecretKey")):
        if not config.get(field):
            raise ValueError("七牛配置：请填写 " + label)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def upload_png(config, png, *, test=False):
    validate(config)
    prefix = config.get("prefix", "").rstrip("/")
    name = time.strftime("report-%Y%m%d-%H%M%S-") + secrets.token_hex(8) + ".png"
    key = "/".join(filter(None, (prefix, "test-" + name if test else name)))
    policy = {"scope": f"{config['bucket']}:{key}", "deadline": int(time.time()) + 600,
              "insertOnly": 1, "fsizeLimit": len(png)}
    if test:
        policy["deleteAfterDays"] = 1
    encoded = base64.urlsafe_b64encode(json.dumps(policy).encode())
    digest = hmac.digest(config["secret_key"].encode(), encoded, "sha1")
    token = ":".join((config["access_key"], base64.urlsafe_b64encode(digest).decode(), encoded.decode()))
    boundary = secrets.token_hex(24)
    fields = [
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n').encode()
        for field, value in (("token", token), ("key", key))
    ]
    fields.extend([
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="report.png"\r\n'
         "Content-Type: image/png\r\n\r\n").encode(), png, f"\r\n--{boundary}--\r\n".encode(),
    ])
    upload = Request("https://" + REGIONS[config["region"]] + "/", data=b"".join(fields),
                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    opener = build_opener(NoRedirect())
    try:
        with opener.open(upload, timeout=25) as response:
            result = json.load(response)
    except HTTPError as error:
        code = error.code
        error.close()
        raise RuntimeError(f"七牛上传失败：HTTP {code}") from None
    except (URLError, OSError, ValueError, HTTPException):
        raise RuntimeError("七牛上传失败：网络或响应异常") from None
    if not isinstance(result, dict) or result.get("key") != key:
        raise RuntimeError("七牛上传失败：返回的文件路径不正确")
    url = config["base_url"].rstrip("/") + "/" + quote(key)
    _verify_download(opener, url, png)
    return url


def _verify_download(opener, url, png):
    # Some CDNs reject the default Python-urllib user agent.
    request = Request(url, headers={"User-Agent": "fnmonitor-report/1.0", "Accept": "image/png"})
    for attempt in range(2):
        stage = "连接下载域名或等待响应"
        try:
            with opener.open(request, timeout=8) as response:
                stage = "读取图片内容"
                if response.headers.get_content_type() != "image/png":
                    raise RuntimeError("七牛图片读回失败：下载域名未返回 PNG")
                downloaded = response.read(len(png) + 1)
                if len(downloaded) < len(png) and response.headers.get("Content-Length") == str(len(png)):
                    raise IncompleteRead(downloaded)
                if downloaded != png:
                    raise RuntimeError("七牛图片读回失败：下载内容与原图不一致")
            return
        except HTTPError as error:
            code = error.code
            error.close()
            raise RuntimeError(f"七牛图片读回失败：HTTP {code}") from None
        except (OSError, HTTPException) as error:
            reason = error.reason if isinstance(error, URLError) else error
            if isinstance(reason, (TimeoutError, ConnectionResetError, IncompleteRead)):
                if attempt == 0:
                    continue  # Re-read the same object without uploading or sending again.
                detail = stage + ("超时" if isinstance(reason, TimeoutError) else "时连接中断")
            elif isinstance(reason, socket.gaierror):
                detail = "无法解析下载域名"
            elif isinstance(reason, ConnectionRefusedError):
                detail = "下载连接被拒绝（请检查飞牛的网络或代理）"
            else:
                detail = stage + "时发生网络或响应异常"
            raise RuntimeError("七牛图片读回失败：" + detail) from None
