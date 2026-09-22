# -*- coding: utf-8 -*-
from parsers.base_parser import BaseParser
from parsers.excel_parser import ExcelAdmissionParser
from parsers.pdf_parser import PdfAdmissionParser
from parsers.docx_parser import DocxAdmissionParser

__all__ = [
    "BaseParser",
    "ExcelAdmissionParser",
    "PdfAdmissionParser",
    "DocxAdmissionParser",
]
