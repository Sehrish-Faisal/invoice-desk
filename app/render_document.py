"""Untrusted file decoder subprocess. No app configuration or credentials imported."""
import json
import sys
import warnings
from pathlib import Path
from PIL import Image, ImageOps
import pypdfium2 as pdf

Image.MAX_IMAGE_PIXELS=20_000_000
warnings.simplefilter('error',Image.DecompressionBombWarning)
def render(source,out):
    head=source.read_bytes()[:16]
    out.mkdir(exist_ok=True)
    if head.startswith(b'%PDF-'):
        d=pdf.PdfDocument(source)
        if d.get_version() is None or not 1<=len(d)<=10: raise ValueError('page count')
        # Refuse password-protected documents, including empty-password encrypted PDFs.
        if pdf.raw.FPDF_GetSecurityHandlerRevision(d.raw)!=-1: raise ValueError('encrypted')
        count=len(d)
        for i in range(count):
            page=d[i]; w,h=page.get_size()
            if min(w,h)<=0 or max(w,h)>14400: raise ValueError('page dimensions')
            scale=min(2.0,1800/max(w,h))
            im=page.render(scale=scale).to_pil().convert('RGB'); im.save(out/f'{i+1}.png')
            page.close()
        d.close(); media='application/pdf'
    elif head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff'):
        with Image.open(source) as original:
            if original.format not in ('PNG','JPEG') or getattr(original,'n_frames',1)!=1: raise ValueError('image format')
            if original.width*original.height>20_000_000: raise ValueError('dimensions')
            original.load(); im=ImageOps.exif_transpose(original).convert('RGB'); im.thumbnail((1800,1800)); im.save(out/'1.png')
            media='image/png' if original.format=='PNG' else 'image/jpeg'
        count=1
    else: raise ValueError('signature')
    if sum(f.stat().st_size for f in out.glob('*.png'))>30*1024*1024: raise ValueError('render size')
    print(json.dumps({'media_type':media,'page_count':count}))
if __name__=='__main__':
    try: render(Path(sys.argv[1]),Path(sys.argv[2]))
    except Exception: print('Document could not be safely decoded.',file=sys.stderr); sys.exit(1)
