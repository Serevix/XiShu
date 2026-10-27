"""读取 CSV 和 XLSX 时保留标识符的文本形式，避免丢失前导零。预览和正式导入共用这些读取方法。"""
from pathlib import Path
import zipfile
import pandas as pd
from backend.core.config import get_settings


def read_frame(path: Path, sheet: str | None = None, encoding: str = 'utf-8-sig', limit: int | None = None):
    """读取表格数据，限制 XLSX 解压后的大小，并拒绝重复列名。"""
    if path.suffix == '.xlsx':
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > get_settings().max_upload_mb * 1024 * 1024 * 10:
                raise ValueError('Excel 解压后体积过大')
        frame = pd.read_excel(path, sheet_name=sheet or 0, dtype=str, keep_default_na=False, nrows=limit)
    else:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding, nrows=limit)
    frame.columns = [str(column).strip() for column in frame.columns]
    if frame.columns.duplicated().any() or any('.1' == column[-2:] for column in frame.columns):
        raise ValueError('表头可能存在重复字段，请修正后上传')
    return frame


def workbook_sheets(path: Path) -> list[str]:
    """读取 Excel 的工作表名称，用于识别各张工作表对应的业务表。"""
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > get_settings().max_upload_mb * 1024 * 1024 * 10:
                raise ValueError('Excel 解压后体积过大')
    except zipfile.BadZipFile:
        raise ValueError('文件不是有效的 XLSX 工作簿') from None
    with pd.ExcelFile(path) as workbook:
        return list(workbook.sheet_names)
