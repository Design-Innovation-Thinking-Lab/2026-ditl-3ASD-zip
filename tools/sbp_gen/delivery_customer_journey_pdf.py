"""Deterministic A3 landscape PDF renderer for Customer Journey delivery."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .delivery import DeliveryError, DeliveryViewModel
from .delivery_customer_journey_markdown import render_customer_journey_markdown
from .delivery_pdf_common import PDFRenderError, _markdown_to_html, _resolve_output_path


CUSTOMER_JOURNEY = "customer_journey"

_PAGE_CSS = """
<style>
@page { size: A3 landscape; margin: 13mm 14mm 17mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0;
  color: #20252b;
  background: #fff;
  font-family: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", "Heiti TC", sans-serif;
  font-size: 9pt;
  line-height: 1.55;
  overflow-wrap: anywhere;
  word-break: normal;
}
h1, h2, h3, h4 {
  color: #172b3a;
  line-height: 1.3;
  break-after: avoid-page;
  page-break-after: avoid;
  overflow-wrap: anywhere;
}
h1 { font-size: 22pt; margin: 0 0 17pt; }
h2 {
  font-size: 15pt;
  margin: 18pt 0 8pt;
  padding-bottom: 4pt;
  border-bottom: 0.6pt solid #bcc9d1;
}
h3 { font-size: 11.5pt; margin: 12pt 0 5pt; }
p { margin: 4pt 0 7pt; orphans: 3; widows: 3; }
ul, ol { margin: 3pt 0 8pt; padding-left: 18pt; }
li { margin: 1pt 0; break-inside: avoid; }
blockquote {
  margin: 5pt 0 7pt;
  padding: 3pt 0 3pt 9pt;
  border-left: 2pt solid #9aaab4;
  color: #34434d;
  break-inside: avoid;
}
blockquote p { margin: 1pt 0; }
code {
  font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
  font-size: 0.84em;
  overflow-wrap: anywhere;
}
table { width: 100%; border-collapse: collapse; table-layout: fixed; margin: 7pt 0 10pt; font-size: 8.2pt; }
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
th, td {
  border: 0.5pt solid #bdc8ce;
  padding: 3.5pt 4pt;
  vertical-align: top;
  text-align: left;
  overflow-wrap: anywhere;
  word-break: break-word;
}
th { background: #edf2f4; font-weight: 600; }
h2#unresolved-mapping, h2#limitations-conflicts, h2#availability {
  border-top: 1.2pt solid #899aa5;
  margin-top: 24pt;
  padding-top: 11pt;
}
@media print {
  a { color: inherit; text-decoration: none; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; }
}
</style>
"""

_FOOTER_TEMPLATE = """
<div style="width:100%;font-family:'Noto Sans TC','PingFang TC',sans-serif;
  font-size:8px;color:#64727a;text-align:center;">
  Customer Journey　｜　第 <span class="pageNumber"></span> 頁／共
  <span class="totalPages"></span> 頁
</div>
"""


def render_customer_journey_pdf(
    view_model: DeliveryViewModel,
    output_path: str | Path,
    *,
    data_root: str | Path | None = None,
) -> Path:
    """Write the normalized Customer Journey hierarchy as A3 landscape PDF."""
    if not isinstance(view_model, DeliveryViewModel):
        raise DeliveryError("renderer_invalid_input", "delivery_view", "expected DeliveryViewModel")
    if view_model.export_id != CUSTOMER_JOURNEY or view_model.delivery_type != CUSTOMER_JOURNEY:
        raise DeliveryError("renderer_unsupported_delivery_type", "delivery_view", view_model.export_id)
    target = _resolve_output_path(output_path, data_root)
    html = _markdown_to_html(render_customer_journey_markdown(view_model), _PAGE_CSS)
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PDFRenderError("pdf_runtime_missing", "Playwright is required for Chromium PDF output") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp.pdf", dir=target.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.set_content(html, wait_until="load")
                    page.pdf(
                        path=str(temporary_path),
                        format="A3",
                        landscape=True,
                        print_background=True,
                        display_header_footer=True,
                        header_template="<div></div>",
                        footer_template=_FOOTER_TEMPLATE,
                        margin={"top": "13mm", "right": "14mm", "bottom": "17mm", "left": "14mm"},
                    )
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise PDFRenderError("pdf_browser_render_failed", str(exc)[:500]) from exc
        if not temporary_path.is_file() or temporary_path.stat().st_size < 100 or not temporary_path.read_bytes().startswith(b"%PDF-"):
            raise PDFRenderError("pdf_browser_render_invalid_output")
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target
