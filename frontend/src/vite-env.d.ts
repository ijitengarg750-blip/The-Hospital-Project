/// <reference types="vite/client" />

declare module "@novnc/novnc" {
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, url: string, options?: { shared?: boolean; wsProtocols?: string[] });
    scaleViewport: boolean;
    resizeSession: boolean;
    disconnect(): void;
    focus(): void;
    sendCtrlAltDel(): void;
  }
}
