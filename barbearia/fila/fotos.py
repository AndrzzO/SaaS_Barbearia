"""Fotos públicas dos profissionais, validadas pelo conteúdo e sem metadados."""
from io import BytesIO
import uuid
from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000


def caminho_foto(instance, filename):
    return f'barbeiros/{uuid.uuid4().hex}.jpg'


def preparar_foto(arquivo):
    if arquivo.size > MAX_BYTES:
        raise ValidationError('A foto deve ter no máximo 5 MB.')
    try:
        arquivo.seek(0)
        with Image.open(arquivo) as imagem:
            if imagem.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValidationError('Envie uma foto JPG, PNG ou WebP.')
            if imagem.width * imagem.height > MAX_PIXELS:
                raise ValidationError('A foto deve ter no máximo 16 megapixels.')
            imagem.load()
            imagem = ImageOps.exif_transpose(imagem)
            imagem.thumbnail((1024, 1024))
            rgba = imagem.convert('RGBA')
            limpa = Image.new('RGB', rgba.size, 'white')
            limpa.paste(rgba, mask=rgba.getchannel('A'))
            saida = BytesIO()
            limpa.save(saida, format='JPEG', quality=88, optimize=True)
        return ContentFile(saida.getvalue(), name='perfil.jpg')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError('Arquivo de imagem inválido. Envie JPG, PNG ou WebP.') from exc
    finally:
        arquivo.seek(0)


def validar_foto(arquivo):
    preparar_foto(arquivo)
