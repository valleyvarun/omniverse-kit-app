from omni.kit.viewport.window import ViewportWindow


class MainWindow:
    def __init__(self) -> None:
        # Use the built-in Omniverse viewport window as the main panel content.
        self._window = ViewportWindow("Viewport")

    def destroy(self) -> None:
        # Tear down the viewport window when the extension shuts down.
        if self._window is not None:
            self._window.destroy()
            self._window = None
