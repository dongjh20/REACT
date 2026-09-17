#!/usr/bin/env python3
"""Serve and verify the real page in Chromium, including fully offline core UI."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site', type=Path, required=True)
    parser.add_argument('--fallback', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        # Range responses are needed to test video seeking, which Python's
        # default SimpleHTTPRequestHandler does not implement.
        def send_head(self):
            path = Path(self.translate_path(self.path))
            self.remaining = None
            if not path.is_file():
                return super().send_head()
            f = path.open('rb')
            size = os.fstat(f.fileno()).st_size
            start, end = 0, size-1
            ranged = self.headers.get('Range')
            if ranged:
                try:
                    low, high = ranged.removeprefix('bytes=').split('-')
                    start = int(low) if low else max(0, size-int(high))
                    end = min(size-1, int(high)) if high and low else size-1
                    if not 0 <= start <= end < size:
                        raise ValueError()
                except ValueError:
                    f.close()
                    self.send_error(416)
                    return None
            self.send_response(206 if ranged else 200)
            self.send_header('Content-Type', self.guess_type(str(path)))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end-start+1))
            if ranged:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            f.seek(start)
            self.remaining = end-start+1
            return f

        def copyfile(self, source, outputfile):
            try:
                if self.remaining is None:
                    return super().copyfile(source, outputfile)
                while self.remaining:
                    chunk = source.read(min(65536, self.remaining))
                    if not chunk:
                        break
                    outputfile.write(chunk)
                    self.remaining -= len(chunk)
            except (ConnectionResetError, BrokenPipeError):
                pass  # Normal cancellation when a browser changes a video range.

        def translate_path(self, path):
            relative = unquote(urlsplit(path).path).lstrip('/')
            if '..' in Path(relative).parts:
                return str(args.site/'not-found')
            chosen = args.site/relative
            if not chosen.exists() and args.fallback:
                chosen = args.fallback/relative
            return str(chosen)

        def log_message(self, *unused):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], timeout=15000)
            for width in (1440, 390):
                context = browser.new_context(viewport={'width': width, 'height': 1000}, device_scale_factor=1)
                # Prove added content works without CDNs, analytics or Google fonts.
                context.route('**/*', lambda route: route.continue_()
                              if route.request.url.startswith(base) else route.abort())
                page = context.new_page()
                errors, failures = [], []
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                page.on('response', lambda r: failures.append([r.status, r.url])
                        if r.url.startswith(base) and r.status >= 400 else None)
                page.goto(base, wait_until='domcontentloaded', timeout=20000)
                page.wait_for_function("window.MathJax && MathJax.startup && MathJax.startup.document && document.querySelectorAll('mjx-container').length > 10", timeout=20000)
                page.evaluate('MathJax.startup.promise')
                assert page.locator('[data-mml-node="merror"]').count() == 0
                assert page.locator('text=Dynamic Obstacle Avoidance').count() == 0
                headings = page.locator('h2').all_text_contents()
                real = next(i for i, t in enumerate(headings) if t == 'Real-World Experiments')
                tvfr = headings.index('Smoother Formation Transitions with TVFR')
                costs = page.locator('h2').evaluate_all(
                    "nodes => nodes.findIndex(node => node.id === 'costs-title')")
                assert real < tvfr < costs
                assert page.locator('h1').inner_text() == 'Demonstration of CFOO'
                assert page.locator('[data-metric="transition-wmr-index"]').inner_text() == 'WMR Indices'
                body = page.locator('body').inner_text()
                for wording in ('changes in the navigable space', 'instantaneous reference update',
                                'first 16 s after transition onset',
                                'Joint Spatio-Temporal Trajectory Planning'):
                    assert wording in body, wording
                section_text = page.locator('#tvfr-comparison').inner_text()
                assert 'Without TVFR' in section_text and 'With TVFR' in section_text
                assert 'earlier version' not in section_text.lower()
                assert 'current TVFR-based' not in section_text
                assert 'Illustrative comparison' not in section_text
                assert 'Source records and measurements' not in section_text
                manifest = json.loads((args.site/'static/data/tvfr_comparison_manifest.json').read_text())
                assert all(r['passed'] is True for r in manifest['runtime_validation'].values())
                for transition in manifest['transitions']:
                    assert transition['environment']['exact_match'] is True
                    assert transition['environment']['cylinder_count'] == 165
                    assert transition['environment']['box_count'] == 4
                    left, right = transition['methods']
                    assert left['label'] == 'Without TVFR' and left['tvfr']['enabled'] is False
                    assert left['tvfr']['transition_duration_seconds'] == 0
                    assert right['label'] == 'With TVFR' and right['tvfr']['enabled'] is True
                    assert right['tvfr']['transition_duration_seconds'] > 0
                assert page.locator('#tvfr-comparison .contrib-metric-tab').count() == 0
                for metric in ('tvfr-trajectories', 'tvfr-transients'):
                    panel = page.locator(f'#metric-{metric}')
                    assert panel.is_visible()
                    panel.scroll_into_view_if_needed()
                    page.wait_for_function(f"""() => {{
                        const i=document.querySelector('#metric-{metric} img');
                        return i.complete && i.naturalWidth > 0;
                    }}""")
                assert page.evaluate("""() => {
                    const first=document.getElementById('metric-tvfr-trajectories').getBoundingClientRect();
                    const second=document.getElementById('metric-tvfr-transients').getBoundingClientRect();
                    return second.top >= first.bottom && first.height > 0 && second.height > 0;
                }""")
                # The paired results and WMR index must remain independently selectable.
                page.locator('[data-metric="transition-wmr-index"]').click()
                assert page.locator('#metric-transition-wmr-index').is_visible()
                assert not page.locator('#metric-transition-formation-error').is_visible()
                page.locator('[data-metric="transition-formation-error"]').click()
                assert not page.locator('#metric-transition-wmr-index').is_visible()
                assert page.locator('#metric-transition-formation-error img:visible').count() == 2
                page.locator('#tvfr-comparison-video').scroll_into_view_if_needed()
                metadata = page.evaluate("""async () => {
                    const v=document.getElementById('tvfr-comparison-video');
                    if(v.readyState < 1) await new Promise((resolve,reject)=>{
                        v.addEventListener('loadedmetadata',resolve,{once:true});
                        v.addEventListener('error',()=>reject(Error('video load error')),{once:true});
                    });
                    v.currentTime=6;
                    await new Promise(resolve=>v.addEventListener('seeked',resolve,{once:true}));
                    if(Math.abs(v.currentTime-6)>.1) throw Error('Initial seek failed');
                    await v.play();
                    await new Promise(resolve=>v.requestVideoFrameCallback(resolve));
                    const first=v.currentTime;
                    await new Promise(resolve=>setTimeout(resolve,300));
                    v.pause();
                    if(v.currentTime<=first) throw Error('Video did not advance');
                    return {duration:v.duration,width:v.videoWidth,height:v.videoHeight,
                            time:v.currentTime,error:v.error,readyState:v.readyState};
                }""")
                assert abs(metadata['duration']-36.) < .05
                assert metadata['width'] == 1920 and metadata['height'] == 642
                page.evaluate("""async () => {
                    const v=document.getElementById('tvfr-comparison-video');
                    v.currentTime=28;
                    await new Promise(resolve=>v.addEventListener('seeked',resolve,{once:true}));
                    if(Math.abs(v.currentTime-28)>.1) throw Error('Second-segment seek failed');
                }""")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                assert page.evaluate("""() => [...document.querySelectorAll('.tvfr-section > .container')]
                    .every(e=>e.getBoundingClientRect().right <= innerWidth+1)""")
                page.locator('#tvfr-comparison').screenshot(path=str(args.output/f'comparison_{width}.png'))
                page.locator('#metric-tvfr-transients').screenshot(path=str(args.output/f'metrics_{width}.png'))
                page.locator('#additional-costs').screenshot(path=str(args.output/f'costs_{width}.png'))
                assert not errors, errors
                assert not failures, failures
                results.append(dict(viewport_width=width, video=metadata,
                                    mathjax_expressions=page.locator('mjx-container').count(),
                                    javascript_errors=errors, local_http_errors=failures,
                                    original_tabs_work=True, comparison_figures_stacked=True,
                                    no_page_overflow=True, external_network_blocked=True))
                context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    report = dict(passed=True, site=str(args.site), results=results)
    (args.output/'browser_verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
