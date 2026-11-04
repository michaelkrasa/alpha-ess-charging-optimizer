// jsdom lacks layout APIs used by the timeline.
class ResizeObserverStub {
  constructor(private cb: ResizeObserverCallback) {}
  observe(target: Element) { this.cb([{ target, contentRect: { width: 1100 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver) }
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
if (!window.matchMedia) window.matchMedia = (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })) as unknown as typeof window.matchMedia
Element.prototype.scrollIntoView = () => {}
