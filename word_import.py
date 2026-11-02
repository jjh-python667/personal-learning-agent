"""读取 DOCX 正文和表格文字；限制大小，不运行文档里的内容。"""
import io
import zipfile
import xml.etree.ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def extract_docx(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo('word/document.xml')
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError('Word正文过大，请拆分后上传。')
            with archive.open(info) as stream:
                xml = stream.read(8 * 1024 * 1024 + 1)
        if len(xml) > 8 * 1024 * 1024:
            raise ValueError('Word正文过大，请拆分后上传。')
        normalized = xml.replace(b'\x00', b'').upper()
        if b'<!DOCTYPE' in normalized or b'<!ENTITY' in normalized:
            raise ValueError('不支持包含自定义XML实体的Word文档。')
        root = ET.fromstring(xml)
        body = root.find(W + 'body')
        if body is None:
            raise ValueError('未找到Word正文。')
        paragraphs = []
        total = 0
        for paragraph in body.iter(W + 'p'):
            parts = []
            for node in paragraph.iter():
                if node.tag == W + 't':
                    parts.append(node.text or '')
                elif node.tag == W + 'tab':
                    parts.append('\t')
                elif node.tag in (W + 'br', W + 'cr'):
                    parts.append('\n')
            text = ''.join(parts).strip()
            total += len(text)
            if total > 300000:
                raise ValueError('Word文字超过30万字，请拆分后上传。')
            if text:
                paragraphs.append(text)
        sections = []
        for paragraph in paragraphs:
            for offset in range(0, len(paragraph), 2500):
                part = paragraph[offset:offset + 2500]
                if sections and len(sections[-1]) + len(part) + 2 <= 2500:
                    sections[-1] += '\n\n' + part
                else:
                    sections.append(part)
        return sections
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError, NotImplementedError) as exc:
        raise ValueError('无法读取Word文档，请确认文件是未加密的.docx格式。') from exc
