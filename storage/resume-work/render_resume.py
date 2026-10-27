"""使用打包渲染器与 Windows Word 导出适配器，检查实际 DOCX 分页。"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
RUNTIME = Path('C:/Users/19344/.cache/codex-runtimes/codex-primary-runtime/dependencies')
SKILL = Path('C:/Users/19344/.codex/plugins/cache/openai-primary-runtime/documents/26.1011.11113/skills/documents')


def word_convert(input_path, user_profile, output_dir, stem, verbose=False):
    """只导出生成的简历，Word 后台运行，不修改任何已有用户文档。"""
    output = Path(output_dir) / (stem + '.pdf')
    command = [str(RUNTIME / 'native/powershell/pwsh.exe'), '-NoProfile', '-File', str(WORK / 'word_export.ps1'),
               '-InputPath', str(Path(input_path).resolve()), '-OutputPdf', str(output.resolve())]
    completed = subprocess.run(command, capture_output=True, timeout=90, encoding='utf-8', errors='replace')
    logs = completed.stdout + '\n' + completed.stderr
    (WORK / 'word-render.log').write_text(logs, encoding='utf-8')
    if completed.returncode or not output.is_file():
        raise RuntimeError('Word export failed: ' + logs)
    return str(output), logs


def main():
    """运行原渲染器的 PDF 转 PNG 流程，Windows 仅替换不存在的 LibreOffice 导出阶段。"""
    os.environ['PATH'] = str(RUNTIME / 'native/poppler/Library/bin') + os.pathsep + os.environ.get('PATH', '')
    spec = importlib.util.spec_from_file_location('packaged_docx_renderer', SKILL / 'render_docx.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.convert_to_pdf = word_convert
    sys.argv = [str(SKILL / 'render_docx.py'), str(ROOT / 'output/简历/韩克迪_简历.docx'),
                '--output_dir', str(WORK / 'render'), '--dpi', '150', '--emit_pdf']
    module.main()


if __name__ == '__main__':
    main()
