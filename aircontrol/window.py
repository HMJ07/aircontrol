"""Ventana de vista previa de OpenCV: flotante (siempre por encima, para que el teclado aéreo se vea sobre otras apps),
reposicionable y en pantalla completa para las calibraciones."""
import sys

import cv2


def _float_macos(title):
    """Nivel de ventana 'flotante' + visible en todos los Spaces + no se oculta al cambiar de app (pyobjc)."""
    from AppKit import (NSApplication, NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorFullScreenAuxiliary)
    for w in NSApplication.sharedApplication().windows():
        if str(w.title()) == title:
            w.setLevel_(NSFloatingWindowLevel)
            w.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                     | NSWindowCollectionBehaviorFullScreenAuxiliary)
            w.setHidesOnDeactivate_(False)
            return True
    return False


class Preview:
    def __init__(self, title):
        self.title = title
        self._floated = False
        self._geometry = None
        self._fullscreen = False
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)

    def _try_float(self):
        if self._floated:
            return
        try:
            if sys.platform == "win32":
                cv2.setWindowProperty(self.title, cv2.WND_PROP_TOPMOST, 1)
                self._floated = True
            elif sys.platform == "darwin":
                self._floated = _float_macos(self.title)
        except Exception:
            self._floated = True                      # no insistir si el SO no lo permite

    def set_geometry(self, x, y, w, h):
        """Mueve y redimensiona la ventana (solo si cambió, para no pelear con el gestor de ventanas)."""
        if self._fullscreen:
            self.set_fullscreen(False)
        if (x, y, w, h) != self._geometry:
            cv2.resizeWindow(self.title, int(w), int(h))
            cv2.moveWindow(self.title, int(x), int(y))
            self._geometry = (x, y, w, h)

    def set_fullscreen(self, on):
        if on != self._fullscreen:
            cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN,
                                  cv2.WINDOW_FULLSCREEN if on else cv2.WINDOW_NORMAL)
            self._fullscreen = on
            self._geometry = None

    def show(self, img):
        cv2.imshow(self.title, img)
        self._try_float()

    def key(self):
        """Procesa eventos de la ventana y devuelve la tecla pulsada (o -1)."""
        return cv2.waitKey(1) & 0xFF

    def closed(self):
        try:
            return cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) < 1
        except cv2.error:
            return True

    def close(self):
        cv2.destroyWindow(self.title)
