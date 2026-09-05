"""Opt-in local browser acceptance: python tests/collector_single_video_ui.py.

Uses the real collector HTTP/UI with synthetic storage and a fake task manager.
No real downloads, creator settings, secrets, or remote services are touched.
"""
from pathlib import Path
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from mx_agent import collector_server
from mx_agent.single_video import normalize_video_link, resolve_video_link
from test_collector_server import CollectorServerTests, FakeStorage, FakeOutbox

VIDEO_ID = "7678988051075051365"
URL = f"https://www.douyin.com/video/{VIDEO_ID}"
SHARE = "5.61 :4pm j@p.qE UyT:/ 06/06 35的紫金矿业包含了什么？ https://v.douyin.com/l1Z_BDCPdMM/ 复制此链接，打开抖音搜索，直接观看视频！"


def fixture_resolve(text):
    normalized = normalize_video_link(text)
    return resolve_video_link(URL if normalized == "https://v.douyin.com/l1Z_BDCPdMM/" else normalized)


def main():
    case = CollectorServerTests()
    case.setUp()
    server = runtime = None
    try:
        media = case.root / "media-private" / "fixture.mp4"
        media.parent.mkdir()
        media.write_bytes(b"fixture")
        runtime, manager = case.runtime(configured=False, storage=FakeStorage(media), outbox=FakeOutbox())
        runtime.registry.creators[0].update(name="实战小周", profile_url="https://www.douyin.com/user/fixture", creator_sync_history_limit=50)
        server = collector_server.create_http_server(runtime, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        runtime.start()
        preview = ROOT / "tmp" / "single-video-acceptance"
        preview.mkdir(parents=True, exist_ok=True)
        with patch.object(collector_server, "resolve_video_link", side_effect=fixture_resolve), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1100}, service_workers="block")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/collector/")
            page.locator("#settingsButton").click()
            page.locator("#settingsVideoUrl").fill(SHARE)
            page.locator("#singleVideoPreview").filter(has_text="已识别视频").wait_for()
            assert manager.run_requests == [], "Pasting a link must not collect"
            assert page.locator("#saveAndRunButton").inner_text() == "保存并抓取这条视频"
            assert page.locator("#runSingleVideoButton").is_enabled()
            page.screenshot(path=str(preview / "desktop.png"), full_page=True)

            # All grabbing controls must target the one video, including the
            # existing footer control. Cancelling confirmation must enqueue none.
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.locator("#runSingleVideoButton").click()
            assert manager.run_requests == []
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#saveAndRunButton").click()
            page.locator("#settingsDialog").wait_for(state="hidden")
            assert manager.run_requests == ["creator-one"]
            assert manager.run_options[0]["video_url"] == URL
            assert runtime.registry.creators[0]["creator_sync_history_limit"] == 50

            page.locator("#settingsButton").click()
            page.locator("#settingsDialog").wait_for(state="visible")
            assert page.locator("#settingsVideoUrl").input_value() == ""
            assert page.locator("#saveAndRunButton").inner_text() == "保存并抓取"
            page.locator("#settingsVideoUrl").fill("https://www.douyin.com/user/not-a-video")
            page.locator('#singleVideoPreview[data-error="true"]').wait_for()
            assert page.locator("#saveAndRunButton").is_disabled()
            assert page.locator("#runSingleVideoButton").is_disabled()
            assert len(manager.run_requests) == 1

            page.locator("#settingsVideoUrl").fill(SHARE)
            page.locator("#singleVideoPreview").filter(has_text="已识别视频").wait_for()
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                page.locator("#settingsVideoUrl").scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("#settingsDialog").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
                assert page.locator("#runSingleVideoButton").is_enabled()
                page.screenshot(path=str(preview / f"mobile-{width}.png"), full_page=True)
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#runSingleVideoButton").click()
            page.locator("#settingsDialog").wait_for(state="hidden")
            assert len(manager.run_requests) == 2
            assert manager.run_options[-1]["video_url"] == URL
            assert not errors, errors
            context.close()
            browser.close()
        print("PASS: auto preview, no automatic collection, confirmation/cancel, selected creator, single target, invalid input, mobile 390/320 px, no browser errors")
        print(preview)
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if runtime:
            runtime.close()
        case.tearDown()


if __name__ == "__main__":
    main()
