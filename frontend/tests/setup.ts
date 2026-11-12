/** Installs a jsdom DOM as globals for node:test. Import first in every test file. */
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', { url: 'http://localhost:5173/' });
const win = dom.window;
const g = globalThis as unknown as Record<string, unknown>;

g.window = win;
g.document = win.document;
for (const name of ['HTMLElement', 'HTMLCanvasElement', 'HTMLButtonElement', 'HTMLDivElement', 'Node', 'AbortController', 'AbortSignal']) {
  g[name] = (win as unknown as Record<string, unknown>)[name];
}
g.getComputedStyle = win.getComputedStyle.bind(win);
// No canvas or animation frames in jsdom; the visualizer simply does not draw.
win.HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement['getContext'];
g.requestAnimationFrame = () => 0;
g.cancelAnimationFrame = () => undefined;
