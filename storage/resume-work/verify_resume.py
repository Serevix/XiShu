"""核对生成简历中的全部文字、原生列表和图片，防止排版过程中遗漏内容。"""
import json
import re
from pathlib import Path
from zipfile import ZipFile
from lxml import etree


def normalize(text):
    """忽略截图转录和排版产生的空格，保留全部文字及标点。"""
    return re.sub(r'\s+', '', text)


def main():
    """逐项检查两段项目介绍、十二条要点及个人信息。"""
    work = Path(__file__).resolve().parent
    content = json.loads((work / 'resume-content.json').read_text(encoding='utf-8'))
    output = work.parents[1] / 'output/简历/韩克迪_简历.docx'
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with ZipFile(output) as package:
        document = etree.fromstring(package.read('word/document.xml'))
        paragraphs = [''.join(p.xpath('.//w:t/text()', namespaces=ns))
                      for p in document.xpath('//w:p', namespaces=ns)]
        complete_text = normalize(''.join(paragraphs))
        expected = [content[key] for key in ['name', 'age', 'email', 'phone', 'education']]
        for project in content['projects']:
            expected.extend([project['title'], project['description'], *project['bullets']])
        for text in expected:
            assert normalize(text) in complete_text, f'缺少文字：{text}'
        assert len(document.xpath('//w:numPr', namespaces=ns)) == 12
        assert len([name for name in package.namelist() if name.startswith('word/media/')]) == 1
    assert 'Pages=1' in (work / 'word-render.log').read_text(encoding='utf-8-sig')
    print(f'核对通过：{len(expected)} 项原文全部保留；12 条原生列表；1 张可替换照片；Word 实际渲染为 1 页。')


if __name__ == '__main__':
    main()
