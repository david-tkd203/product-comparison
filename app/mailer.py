import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from flask import render_template

SMTP_HOST = os.environ.get('SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_FROM = os.environ.get('SMTP_FROM', 'Siyash Perfumería <contacto@siyash.cl>')
SMTP_SECURE = os.environ.get('SMTP_SECURE', 'tls')  # 'tls' or 'ssl'


def send_email(to_email, subject, html_content, attachments=None):
    """
    Sends an email using standard SMTP.
    Supports attachments as a list of tuples: [(filename, file_bytes, mime_type), ...]
    If SMTP credentials are not configured, it safely logs the attempt without crashing.
    """
    if not SMTP_HOST or not SMTP_USER:
        print(f"[MAILER DRY-RUN] Would send '{subject}' to <{to_email}> (SMTP not configured).")
        return True

    try:
        if attachments:
            msg = MIMEMultipart('mixed')
            alt_part = MIMEMultipart('alternative')
            alt_part.attach(MIMEText(html_content, 'html', 'utf-8'))
            msg.attach(alt_part)

            for att in attachments:
                filename = att[0]
                file_bytes = att[1]
                part = MIMEApplication(file_bytes, Name=filename)
                part['Content-Disposition'] = f'attachment; filename="{filename}"'
                msg.attach(part)
        else:
            msg = MIMEMultipart('alternative')
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email

        if SMTP_SECURE == 'ssl' or SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=12)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12)
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


def send_order_placed_email(to_email, order_data):
    """Disparado inmediatamente al registrar la orden en la web."""
    try:
        html = render_template(
            'emails/pedido_realizado.html',
            order_id=order_data.get('id'),
            nombre=order_data.get('nombre'),
            items=order_data.get('items', []),
            subtotal=order_data.get('subtotal'),
            descuento=order_data.get('descuento', 0),
            cupon_codigo=order_data.get('cupon_codigo'),
            total=order_data.get('total', 0)
        )
        return send_email(to_email, f"Pedido #{order_data.get('id')} Registrado con Éxito | Siyash Perfumería", html)
    except Exception as e:
        print(f"[MAILER ERROR] render order placed email failed: {e}")
        return False


def send_order_confirmation_email(to_email, order_data, attach_boleta=True):
    """Disparado cuando el pago está confirmado. Genera y adjunta la Boleta PDF."""
    try:
        attachments = None
        if attach_boleta:
            try:
                from .boleta import generate_boleta_pdf
                pdf_bytes = generate_boleta_pdf(order_data)
                order_num = order_data.get('id', '000')
                attachments = [(f"Boleta_Siyash_{order_num:06d}.pdf", pdf_bytes, "application/pdf")]
            except Exception as e:
                print(f"[MAILER WARNING] No se pudo generar la boleta PDF: {e}")

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
        subject = f"Pago Confirmado y Boleta #{order_data.get('id')} | Siyash Perfumería"
        return send_email(to_email, subject, html, attachments=attachments)
    except Exception as e:
        print(f"[MAILER ERROR] render order confirmation email failed: {e}")
        return False


def send_order_shipped_email(to_email, tracking_data):
    try:
        html = render_template(
            'emails/pedido_enviado.html',
            order_id=tracking_data.get('id'),
            nombre=tracking_data.get('nombre'),
            courier=tracking_data.get('courier', 'Starken'),
            tracking_number=tracking_data.get('tracking_number'),
            tracking_url=tracking_data.get('tracking_url'),
            direccion_envio=tracking_data.get('direccion_envio')
        )
        return send_email(to_email, f"Tu Pedido #{tracking_data.get('id')} va en Camino | Siyash", html)
    except Exception as e:
        print(f"[MAILER ERROR] render order shipped email failed: {e}")
        return False


def send_order_delivered_email(to_email, order_data):
    try:
        html = render_template(
            'emails/pedido_entregado.html',
            order_id=order_data.get('id'),
            nombre=order_data.get('nombre'),
            fecha_entrega=order_data.get('fecha_entrega')
        )
        return send_email(to_email, f"¡Tu Pedido #{order_data.get('id')} ha sido Entregado! | Siyash", html)
    except Exception as e:
        print(f"[MAILER ERROR] render order delivered email failed: {e}")
        return False


def send_order_cancelled_email(to_email, order_data):
    try:
        html = render_template(
            'emails/pedido_cancelado.html',
            order_id=order_data.get('id'),
            nombre=order_data.get('nombre'),
            motivo_cancelacion=order_data.get('motivo_cancelacion')
        )
        return send_email(to_email, f"Actualización: Pedido #{order_data.get('id')} Cancelado | Siyash", html)
    except Exception as e:
        print(f"[MAILER ERROR] render order cancelled email failed: {e}")
        return False


def send_refund_email(to_email, refund_data):
    try:
        html = render_template(
            'emails/devolucion_reembolso.html',
            order_id=refund_data.get('id'),
            nombre=refund_data.get('nombre'),
            monto_reembolsado=refund_data.get('monto_reembolsado'),
            tipo_resolucion=refund_data.get('tipo_resolucion', 'Transferencia Bancaria'),
            numero_comprobante=refund_data.get('numero_comprobante')
        )
        return send_email(to_email, f"Comprobante de Devolución Orden #{refund_data.get('id')} | Siyash", html)
    except Exception as e:
        print(f"[MAILER ERROR] render refund email failed: {e}")
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


def send_abandoned_cart_email(to_email, nombre, items, cart_url):
    try:
        html = render_template(
            'emails/carrito_abandonado.html',
            nombre=nombre,
            items=items,
            cart_url=cart_url
        )
        return send_email(to_email, "Tus fragancias favoritas te están esperando | Siyash", html)
    except Exception as e:
        print(f"[MAILER ERROR] render abandoned cart email failed: {e}")
        return False


def send_password_reset_email(to_email, nombre, reset_link, expira_horas=24):
    try:
        html = render_template(
            'emails/recuperar_password.html',
            nombre=nombre,
            reset_link=reset_link,
            expira_horas=expira_horas
        )
        return send_email(to_email, "Restablece tu Contraseña | Club Siyash", html)
    except Exception as e:
        print(f"[MAILER ERROR] render password reset email failed: {e}")
        return False


def send_account_deleted_email(to_email, nombre):
    try:
        html = render_template(
            'emails/cuenta_eliminada.html',
            nombre=nombre,
            email=to_email
        )
        return send_email(to_email, "Confirmación de Eliminación de Cuenta | Siyash Perfumería", html)
    except Exception as e:
        print(f"[MAILER ERROR] render account deleted email failed: {e}")
        return False


def send_announcement_email(to_email, announcement_data):
    try:
        html = render_template(
            'emails/comunicado.html',
            titulo_comunicado=announcement_data.get('titulo', 'Comunicado Oficial'),
            tag_comunicado=announcement_data.get('tag', 'AVISO OFICIAL'),
            subtitulo=announcement_data.get('subtitulo', ''),
            nombre=announcement_data.get('nombre'),
            cuerpo_comunicado=announcement_data.get('cuerpo', ''),
            cta_url=announcement_data.get('cta_url'),
            cta_texto=announcement_data.get('cta_texto', 'VER MÁS')
        )
        return send_email(to_email, f"{announcement_data.get('titulo', 'Comunicado Oficial')} | Siyash", html)
    except Exception as e:
        print(f"[MAILER ERROR] render announcement email failed: {e}")
        return False
