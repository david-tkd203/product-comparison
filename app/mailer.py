import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import render_template

SMTP_HOST = os.environ.get('SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_FROM = os.environ.get('SMTP_FROM', 'Siyash Perfumería <contacto@siyash.cl>')
SMTP_SECURE = os.environ.get('SMTP_SECURE', 'tls')  # 'tls' or 'ssl'


def send_email(to_email, subject, html_content):
    """
    Sends an email using standard SMTP.
    If SMTP credentials are not configured, it safely logs the attempt without crashing.
    """
    if not SMTP_HOST or not SMTP_USER:
        print(f"[MAILER DRY-RUN] Would send '{subject}' to <{to_email}> (SMTP not configured).")
        return True

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email

        part = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(part)

        if SMTP_SECURE == 'ssl' or SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
            server.starttls()

        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        server.quit()
        print(f"[MAILER] Email '{subject}' successfully sent to <{to_email}>.")
        return True
    except Exception as e:
        print(f"[MAILER ERROR] Failed to send email to <{to_email}>: {e}")
        return False


def send_welcome_email(to_email, nombre):
    try:
        html = render_template('emails/bienvenida.html', nombre=nombre)
        return send_email(to_email, "Bienvenido al Club Siyash | +100 Puntos de Regalo", html)
    except Exception as e:
        print(f"[MAILER ERROR] render welcome email failed: {e}")
        return False


def send_order_confirmation_email(to_email, order_data):
    try:
        html = render_template(
            'emails/pedido_confirmado.html',
            order_id=order_data.get('id'),
            nombre=order_data.get('nombre'),
            items=order_data.get('items', []),
            subtotal=order_data.get('subtotal'),
            descuento=order_data.get('descuento', 0),
            cupon_codigo=order_data.get('cupon_codigo'),
            total=order_data.get('total', 0),
            puntos_ganados=order_data.get('puntos_ganados', 0)
        )
        return send_email(to_email, f"Confirmación de Pedido #{order_data.get('id')} | Siyash Perfumería", html)
    except Exception as e:
        print(f"[MAILER ERROR] render order confirmation email failed: {e}")
        return False


def send_reward_redemption_email(to_email, nombre, reward_name, code, spent, remaining):
    try:
        html = render_template(
            'emails/canje_premio.html',
            nombre=nombre,
            premio_nombre=reward_name,
            codigo_canje=code,
            puntos_gastados=spent,
            puntos_restantes=remaining
        )
        return send_email(to_email, f"Tu código de regalo {code} | Club Siyash", html)
    except Exception as e:
        print(f"[MAILER ERROR] render reward redemption email failed: {e}")
        return False


def send_promo_coupon_email(to_email, nombre, promo_data):
    try:
        html = render_template(
            'emails/promocion_cupon.html',
            nombre=nombre,
            titulo_promo=promo_data.get('titulo_promo', 'Beneficio Exclusivo'),
            tag_promo=promo_data.get('tag_promo', 'CLUB SIYASH VIP'),
            valor_descuento=promo_data.get('valor_descuento', '15% DE DESCUENTO'),
            codigo_cupon=promo_data.get('codigo_cupon', 'CYBERSIYASH'),
            minimo_compra=promo_data.get('minimo_compra', ''),
            fecha_expiracion=promo_data.get('fecha_expiracion', '')
        )
        return send_email(to_email, f"{promo_data.get('titulo_promo', 'Beneficio Exclusivo')} | Siyash Perfumería", html)
    except Exception as e:
        print(f"[MAILER ERROR] render promo coupon email failed: {e}")
        return False
