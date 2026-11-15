"""Generate clearly labelled synthetic documents; no real financial data."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
root=Path(__file__).resolve().parent
fontpath='C:/Windows/Fonts/arial.ttf'
def font(size): return ImageFont.truetype(fontpath,size)
for number,quantity,tax in [('DEMO-1042',20,0),('DEMO-1043',8,100)]:
    im=Image.new('RGB',(1200,1550),'white'); d=ImageDraw.Draw(im)
    d.text((85,75),'NORTHSTAR',font=font(44),fill='#173e42');d.text((85,135),'Demo — Northstar Supplies',font=font(25),fill='#536d6e')
    d.text((85,230),'INVOICE',font=font(60),fill='#173e42');d.text((85,315),'SYNTHETIC DEMONSTRATION — NOT PAYABLE',font=font(20),fill='#a36c34')
    for y,label,value in [(420,'Invoice number',number),(475,'Invoice date','2026-11-13'),(530,'Due date','2026-12-13'),(585,'Purchase order','PO-2026-001'),(640,'Currency','PKR')]:
        d.text((85,y),label,font=font(24),fill='#71817e');d.text((650,y),value,font=font(26),fill='#243e3c')
    d.rectangle((85,735,1115,795),fill='#ecf2ef')
    for x,label in [(105,'Description'),(600,'Quantity'),(780,'Unit price'),(960,'Amount')]:d.text((x,750),label,font=font(22),fill='#355a53')
    for x,value in [(105,'Packaging bags'),(620,str(quantity)),(795,'125.50'),(960,f'{quantity*125.5:.2f}')]:d.text((x,830),value,font=font(24),fill='#263e3b')
    for y,label,value in [(970,'Subtotal',quantity*125.5),(1025,'Tax',tax),(1080,'Discount',0),(1180,'TOTAL DUE',quantity*125.5+tax)]:
        d.text((650,y),label,font=font(26),fill='#33554e');d.text((960,y),f'{value:.2f}',font=font(26),fill='#173e42')
    d.text((85,1420),'Example document for InvoiceDesk portfolio testing only.',font=font(20),fill='#7c8d87')
    im.save(root/f'{number}.png');im.save(root/f'{number}.pdf','PDF',resolution=144)
print('Generated two synthetic PNG invoices and two image-based PDFs.')
