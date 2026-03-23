import smtplib
import ssl
import os
import logging
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# 确保从 api-server/.env 加载，无论工作目录在哪
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "ClawSync")

# 启动时打印配置确认（不打印密码）
logger.info(f"SMTP 配置: host={SMTP_HOST}, port={SMTP_PORT}, user={SMTP_USER}")
print(f"[SMTP] host={SMTP_HOST}, port={SMTP_PORT}, user={SMTP_USER}, password={'***' if SMTP_PASSWORD else 'EMPTY!'}")


def send_verification_email(to_email: str, code: str) -> bool:
    """发送验证码邮件，成功返回 True，失败返回 False"""
    subject = f"【{SMTP_FROM_NAME}】邮箱验证码"
    html = f"""
    <div style="max-width:400px;margin:0 auto;padding:30px;font-family:sans-serif;">
        <h2 style="color:#333;">邮箱验证</h2>
        <p>您的验证码为：</p>
        <div style="font-size:32px;font-weight:bold;letter-spacing:8px;
                    color:#1a73e8;background:#f0f4ff;padding:16px;
                    border-radius:8px;text-align:center;margin:20px 0;">
            {code}
        </div>
        <p style="color:#666;font-size:14px;">验证码 5 分钟内有效，请勿泄露给他人。</p>
        <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
        <p style="color:#999;font-size:12px;">此邮件由 {SMTP_FROM_NAME} 系统自动发送，请勿回复。</p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=10) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        logger.info(f"验证码邮件已发送至 {to_email}")
        return True
    except Exception as e:
        logger.error(f"发送邮件失败 ({to_email}): {e}")
        return False
