"""
app/boleta.py — Generador de Boleta Electrónica PDF oficial para Siyash Perfumería.
Genera un documento PDF vectorial de alta fidelidad utilizando ReportLab.
"""

import io
import os
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.shapes import Drawing, Rect, String, Line
from reportlab.graphics.barcode import qr


def generate_boleta_pdf(order_data):
    """
    Genera el PDF vectorial de la boleta de compra.
    Retorna un buffer bytes con el contenido del PDF listo para descargar o adjuntar.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm
    )

    styles = getSampleStyleSheet()

    # Estilos tipográficos inspirados en la estética de Siyash
    style_normal = ParagraphStyle(
        'NormalSiyash',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#2d2d2e')
    )

    style_bold = ParagraphStyle(
        'BoldSiyash',
        parent=style_normal,
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#000000')
    )

    style_emisor_title = ParagraphStyle(
        'EmisorTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#000000')
    )

    style_emisor_sub = ParagraphStyle(
        'EmisorSub',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#5f5f5f')
    )

    style_fiscal_box = ParagraphStyle(
        'FiscalBox',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        alignment=1, # Centered
        textColor=colors.HexColor('#991b1b') # Rojo burdeos formal fiscal
    )

    style_table_header = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#ffffff')
    )

    style_table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#1c1917')
    )

    style_table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=style_table_cell,
        fontName='Helvetica-Bold'
    )

    style_table_cell_right = ParagraphStyle(
        'TableCellRight',
        parent=style_table_cell,
        alignment=2 # Right
    )

    story = []

    order_id = order_data.get('id') or order_data.get('order_id', 1)
    folio_str = f"SIY-{int(order_id):06d}"
    fecha_emision = order_data.get('fecha') or datetime.now().strftime("%d/%m/%Y %H:%M")
    nombre_cliente = order_data.get('nombre') or 'Cliente General'
    telefono_cliente = order_data.get('telefono') or 'No especificado'
    email_cliente = order_data.get('email') or 'contacto@siyash.cl'
    metodo_pago = order_data.get('metodo_pago') or 'Shopify Payments / Webpay'

    # --- 1. CABECERA: LOGO / EMISOR (IZQ) + RECUADRO FISCAL (DER) ---
    logo_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'img', 'LogoSiyash-01-t.png')
    emisor_elements = []
    
    if os.path.exists(logo_path):
        try:
            # 140px por aprox 40px
            emisor_elements.append(Image(logo_path, width=4.5 * cm, height=1.6 * cm))
            emisor_elements.append(Spacer(1, 4))
        except Exception:
            pass

    emisor_elements.append(Paragraph("<b>SIYASH PERFUMERÍA SpA</b>", style_emisor_title))
    emisor_elements.append(Paragraph("Venta de Perfumes Originales por Mayor y Detalle", style_emisor_sub))
    emisor_elements.append(Paragraph("Santiago de Chile &bull; contacto@siyash.cl &bull; www.siyash.cl", style_emisor_sub))
    emisor_elements.append(Paragraph("Mesa Central: +56 9 3223 8816", style_emisor_sub))

    # Recuadro fiscal derecho (Formato oficial chileno)
    fiscal_content = [
        Paragraph("<b>R.U.T.: 77.892.340-5</b>", style_fiscal_box),
        Paragraph("<b>BOLETA ELECTRÓNICA</b>", style_fiscal_box),
        Paragraph(f"<b>N° {folio_str}</b>", style_fiscal_box),
        Spacer(1, 2),
        Paragraph("<font size=7 color='#7f1d1d'>S.I.I. - SANTIAGO CENTRO</font>", style_fiscal_box)
    ]

    fiscal_table = Table([[fiscal_content]], colWidths=[6.5 * cm])
    fiscal_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor('#991b1b')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fef2f2')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))

    header_table = Table([[emisor_elements, fiscal_table]], colWidths=[10.5 * cm, 7.5 * cm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 14))

    # --- 2. DATOS DEL CLIENTE Y FECHA ---
    cliente_data = [
        [
            Paragraph("<b>SEÑOR(A):</b>", style_bold),
            Paragraph(nombre_cliente.upper(), style_normal),
            Paragraph("<b>FECHA EMISIÓN:</b>", style_bold),
            Paragraph(fecha_emision, style_normal)
        ],
        [
            Paragraph("<b>TELÉFONO:</b>", style_bold),
            Paragraph(telefono_cliente, style_normal),
            Paragraph("<b>FORMA DE PAGO:</b>", style_bold),
            Paragraph(metodo_pago, style_normal)
        ],
        [
            Paragraph("<b>EMAIL:</b>", style_bold),
            Paragraph(email_cliente, style_normal),
            Paragraph("<b>ESTADO PAGO:</b>", style_bold),
            Paragraph("<font color='#15803d'><b>PAGADO / APROBADO</b></font>", style_normal)
        ]
    ]

    cliente_table = Table(cliente_data, colWidths=[2.6 * cm, 6.4 * cm, 3.2 * cm, 5.8 * cm])
    cliente_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e2e2')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fafafa')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(cliente_table)
    story.append(Spacer(1, 14))

    # --- 3. DETALLE DE FRAGANCIAS / PRODUCTOS ---
    items = order_data.get('items') or []
    items_table_data = [
        [
            Paragraph("SKU", style_table_header),
            Paragraph("DESCRIPCIÓN / FRAGANCIA", style_table_header),
            Paragraph("CANT", style_table_header),
            Paragraph("P. UNIT.", style_table_header),
            Paragraph("TOTAL", style_table_header)
        ]
    ]

    total_bruto = 0

    if items:
        for it in items:
            sku = str(it.get('sku') or 'PERF')
            nombre = it.get('nombre') or 'Fragancia Original'
            linea = it.get('linea') or ''
            qty = int(it.get('qty') or 1)
            precio = int(it.get('precio') or 0)
            item_total = precio * qty
            total_bruto += item_total

            desc_text = f"<b>{nombre}</b>"
            if linea:
                desc_text += f"<br/><font color='#797979' size=7.5>{linea.upper()}</font>"

            if precio == 0:
                p_unit_str = "PREMIO"
                tot_str = "$0"
            else:
                p_unit_str = f"${precio:,.0f}".replace(',', '.')
                tot_str = f"${item_total:,.0f}".replace(',', '.')

            items_table_data.append([
                Paragraph(sku, style_table_cell),
                Paragraph(desc_text, style_table_cell),
                Paragraph(str(qty), ParagraphStyle('Center', parent=style_table_cell, alignment=1)),
                Paragraph(p_unit_str, style_table_cell_right),
                Paragraph(tot_str, style_table_cell_right)
            ])
    else:
        items_table_data.append([
            Paragraph("-", style_table_cell),
            Paragraph("Compra de fragancias en Siyash.cl", style_table_cell),
            Paragraph("1", style_table_cell),
            Paragraph(f"${order_data.get('total', 0):,.0f}".replace(',', '.'), style_table_cell_right),
            Paragraph(f"${order_data.get('total', 0):,.0f}".replace(',', '.'), style_table_cell_right)
        ])
        total_bruto = int(order_data.get('total', 0))

    items_table = Table(
        items_table_data,
        colWidths=[2.6 * cm, 8.4 * cm, 1.8 * cm, 2.6 * cm, 2.6 * cm]
    )
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#000000')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#ffffff')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#d4d4d4')),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#e5e5e5')),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 12))

    # --- 4. TOTALES, IVA Y PUNTOS CLUB SIYASH ---
    total = int(order_data.get('total', total_bruto))
    descuento = int(order_data.get('descuento') or 0)
    cupon_codigo = order_data.get('cupon_codigo') or ''
    puntos_ganados = int(order_data.get('puntos_ganados') or (total // 100))

    # Desglose legal chileno: Total = Neto + IVA (19%)
    # Neto = Total / 1.19
    monto_neto = int(round(total / 1.19))
    monto_iva = total - monto_neto

    resumen_data = [
        [
            Paragraph("<b>MONTO NETO:</b>", style_table_cell_bold),
            Paragraph(f"${monto_neto:,.0f}".replace(',', '.'), style_table_cell_right)
        ],
        [
            Paragraph("<b>I.V.A. (19%):</b>", style_table_cell_bold),
            Paragraph(f"${monto_iva:,.0f}".replace(',', '.'), style_table_cell_right)
        ]
    ]

    if descuento > 0:
        desc_label = f"DESCUENTO ({cupon_codigo})" if cupon_codigo else "DESCUENTO CUPÓN"
        resumen_data.append([
            Paragraph(f"<b><font color='#15803d'>{desc_label}:</font></b>", style_table_cell_bold),
            Paragraph(f"<font color='#15803d'>-${descuento:,.0f}</font>".replace(',', '.'), style_table_cell_right)
        ])

    resumen_data.append([
        Paragraph("<b>TOTAL PAGADO (CLP):</b>", ParagraphStyle('TotLabel', parent=style_bold, fontSize=11)),
        Paragraph(f"<b>${total:,.0f}</b>".replace(',', '.'), ParagraphStyle('TotVal', parent=style_bold, fontSize=13, alignment=2))
    ])

    resumen_table = Table(resumen_data, colWidths=[4.2 * cm, 3.8 * cm])
    resumen_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#000000')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f5f5f5')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))

    # Panel izquierdo con información de fidelización Club Siyash
    club_info = [
        Paragraph("<b>BENEFICIOS CLUB SIYASH</b>", ParagraphStyle('ClubTit', parent=style_bold, fontSize=8.5, textColor=colors.HexColor('#000000'))),
        Spacer(1, 2),
        Paragraph(f"Puntos acreditados con esta compra: <b>+{puntos_ganados} pts</b>", style_normal),
        Paragraph("<font color='#5f5f5f' size=7.5>Acumulaste 1 punto por cada $100 CLP. Canjeables en tu cuenta por fragancias y regalos.</font>", style_normal),
        Spacer(1, 4),
        Paragraph("<font color='#000000' size=7.5><b>Garantía:</b> 100% Original con sello de importación oficial.</font>", style_normal)
    ]
    club_table = Table([[club_info]], colWidths=[9.5 * cm])
    club_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e2e2')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fafafa')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))

    bottom_block = Table([[club_table, resumen_table]], colWidths=[10.0 * cm, 8.0 * cm])
    bottom_block.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(bottom_block)
    story.append(Spacer(1, 16))

    # --- 5. TIMBRE ELECTRÓNICO SII (Simulado oficial) ---
    qr_code = qr.QrCodeWidget(f"https://siyash.cl/pedido/{order_id}/boleta?folio={folio_str}&monto={total}")
    qr_code.barWidth = 45
    qr_code.barHeight = 45
    qr_code.qrVersion = 1
    qr_drawing = Drawing(45, 45)
    qr_drawing.add(qr_code)

    timbre_text = [
        Paragraph("<b>Timbre Electrónico S.I.I.</b>", ParagraphStyle('TimbreTit', parent=style_bold, fontSize=7.5, alignment=1)),
        Paragraph("<font size=6.5 color='#555555'>Res. N° 80 de 2014 - Verifique documento en www.sii.cl o siyash.cl</font>", ParagraphStyle('TimbreSub', parent=style_normal, fontSize=6.5, alignment=1)),
        Spacer(1, 2),
        Paragraph(f"<font size=6.5 color='#000000'>Folio: {folio_str} &bull; Emisión Oficial Siyash Perfumería SpA</font>", ParagraphStyle('TimbreFol', parent=style_normal, fontSize=6.5, alignment=1))
    ]

    timbre_box = Table(
        [[qr_drawing, timbre_text]],
        colWidths=[2.2 * cm, 15.8 * cm]
    )
    timbre_box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#000000')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ffffff')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))

    story.append(KeepTogether([timbre_box]))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
