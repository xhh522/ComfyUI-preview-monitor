import os
import numpy as np
from PIL import Image
from threading import Thread, Lock
import time
import hashlib

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    print("Warning: pygame not installed. PreviewImageMonitor will not display images.")

try:
    from screeninfo import get_monitors
    SCREENINFO_AVAILABLE = True
except ImportError:
    SCREENINFO_AVAILABLE = False


class PreviewImageMonitor:
    """
    Display an image in a persistent fullscreen window on a selected monitor.
    Live updates every prompt, supports multiple fit modes (Nuke style).
    """
    _windows = {}  # monitor_idx -> {"thread": Thread, "images": [PIL.Image], "current_idx": int, "lock": Lock, "visible": bool, "display_mode": str, "compare_image": [PIL.Image], "zoom": float, "pan_x": float, "pan_y": float}

    def __init__(self):
        if PYGAME_AVAILABLE and not pygame.get_init():
            pygame.init()
    
    @classmethod
    def cleanup_all_windows(cls):
        """Clean up all windows and reset pygame state"""
        if cls._windows:
            print("Preview Monitor: Cleaning up all windows")
            for display_idx in list(cls._windows.keys()):
                try:
                    with cls._windows[display_idx]["lock"]:
                        cls._windows[display_idx]["running"] = False
                    cls._windows[display_idx]["thread"].join(timeout=1.0)
                    del cls._windows[display_idx]
                except Exception as e:
                    print(f"Preview Monitor: Error cleaning up window {display_idx}: {e}")
            cls._windows.clear()
        
        if PYGAME_AVAILABLE and pygame.get_init():
            try:
                pygame.quit()
                print("Preview Monitor: Pygame quit")
            except Exception as e:
                print(f"Preview Monitor: Error quitting pygame: {e}")

    @classmethod
    def INPUT_TYPES(cls):
        monitor_list = cls.get_monitors()
        fit_modes = ["none", "width", "height", "fit", "fill", "distort", "center"]
        display_modes = ["single", "comparison", "slideshow"]
        return {
            "required": {
                "images": ("IMAGE",),
                "monitor": (monitor_list, {"default": monitor_list[0]}),
                "power_state": (["On", "Off"], {"default": "On"}),
                "display_mode": (display_modes, {"default": "single"}),
                "fit_mode": (fit_modes, {"default": "fit"}),
                "target_resolution": (["1920x1080", "3840x2160"], {"default": "1920x1080"}),
                "gain": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.1}),
                "gamma": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.1}),
                "saturation": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.1}),
                "white_matte": ("BOOLEAN", {"default": False}),
                "fps_mode": (["smart", "15fps", "30fps"], {"default": "smart"}),
            },
            "optional": {
                "compare_image": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "display_image"
    OUTPUT_NODE = True
    CATEGORY = "image/preview"

    @classmethod
    def get_monitors(cls):
        monitors = []
        if SCREENINFO_AVAILABLE:
            try:
                actual_monitors = get_monitors()
                for i, m in enumerate(actual_monitors):
                    monitors.append(f"Monitor {i} ({m.width}x{m.height})")
            except Exception:
                pass
        if PYGAME_AVAILABLE and not monitors:
            try:
                num_displays = pygame.display.get_num_displays()
                for i in range(num_displays):
                    info = pygame.display.Info(display=i)
                    monitors.append(f"Monitor {i} ({info.current_w}x{info.current_h})")
            except Exception:
                pass
        # Ensure list has 6 monitors (0-5)
        for i in range(len(monitors), 6):
            monitors.append(f"Monitor {i} (unknown resolution)")
        return monitors

    def display_image(self, images, monitor, power_state="On", display_mode="single", fit_mode="fit", target_resolution="1920x1080", gain=1.0, gamma=1.0, saturation=1.0, white_matte=False, fps_mode="smart", compare_image=None):
        if not PYGAME_AVAILABLE:
            print("pygame not available, cannot display preview.")
            return (images,)

        # Determine monitor index
        try:
            display_idx = int(monitor.split(" ")[1])
        except Exception:
            display_idx = 0

        # Parse target resolution
        try:
            res_w, res_h = map(int, target_resolution.split("x"))
        except Exception:
            res_w, res_h = 1920, 1080

        # --- MONITOR SWITCH SAFETY: Auto power off when monitor changes ---
        # If any window exists on a different monitor, power it off
        if self._windows and power_state == "On":
            for existing_idx in list(self._windows.keys()):
                if existing_idx != display_idx:
                    print(f"Preview Monitor: Switching to {monitor}. Powering off previous monitor.")
                    # Power off the window on the old monitor
                    with self._windows[existing_idx]["lock"]:
                        self._windows[existing_idx]["running"] = False
                    self._windows[existing_idx]["thread"].join(timeout=0.5)
                    del self._windows[existing_idx]
                    print(f"Preview Monitor: Previous monitor {existing_idx} closed. Creating window on monitor {display_idx}.")
        # --- END MONITOR SWITCH SAFETY ---

        if power_state == "Off":
            # Gracefully shut down pygame for this monitor
            if display_idx in self._windows:
                # Set running flag to False and wait for thread to terminate
                with self._windows[display_idx]["lock"]:
                    self._windows[display_idx]["running"] = False
                # Wait for thread to finish
                self._windows[display_idx]["thread"].join(timeout=0.5)
                # Clean up the window entry
                del self._windows[display_idx]
                # Don't quit pygame completely to allow new windows
            return (images,)

        # Convert tensor to PIL Images
        pil_images = []
        for image in images:
            if hasattr(image, 'cpu'):
                # PyTorch tensor
                image = 255.0 * image.cpu().numpy()
            elif hasattr(image, 'numpy'):
                # TensorFlow tensor
                image = 255.0 * image.numpy()
            else:
                # Already numpy array
                image = 255.0 * image
            
            # Ensure correct shape for PIL Image
            if len(image.shape) == 4:
                # Remove batch dimension if present
                image = image.squeeze(0)
            if len(image.shape) == 3 and image.shape[2] == 3:
                # Ensure correct channel order (height, width, channels)
                pass
            else:
                raise ValueError(f"Unsupported image shape: {image.shape}")
            
            pil_img = Image.fromarray(np.clip(image, 0, 255).astype(np.uint8))
            # Apply image adjustments
            adjusted_img = self._apply_image_adjustments(pil_img, gain, gamma, saturation)
            pil_images.append(adjusted_img)
        
        # Process compare images if provided
        compare_pil_images = []
        if compare_image is not None:
            for image in compare_image:
                if hasattr(image, 'cpu'):
                    # PyTorch tensor
                    image = 255.0 * image.cpu().numpy()
                elif hasattr(image, 'numpy'):
                    # TensorFlow tensor
                    image = 255.0 * image.numpy()
                else:
                    # Already numpy array
                    image = 255.0 * image
                
                # Ensure correct shape for PIL Image
                if len(image.shape) == 4:
                    # Remove batch dimension if present
                    image = image.squeeze(0)
                if len(image.shape) == 3 and image.shape[2] == 3:
                    # Ensure correct channel order (height, width, channels)
                    pass
                else:
                    raise ValueError(f"Unsupported image shape: {image.shape}")
                
                pil_img = Image.fromarray(np.clip(image, 0, 255).astype(np.uint8))
                adjusted_img = self._apply_image_adjustments(pil_img, gain, gamma, saturation)
                compare_pil_images.append(adjusted_img)

        # Create persistent window if it doesn't exist
        if display_idx not in self._windows:
            print(f"Preview Monitor: Creating window on monitor {display_idx}")
            lock = Lock()
            self._windows[display_idx] = {
                "images": pil_images,
                "current_idx": 0,
                "lock": lock,
                "visible": True,
                "running": True,
                "display_mode": display_mode,
                "compare_image": compare_pil_images,
                "fit_mode": fit_mode,
                "res_w": res_w,
                "res_h": res_h,
                "gain": gain,
                "gamma": gamma,
                "saturation": saturation,
                "white_matte": white_matte,
                "fps_mode": fps_mode,
                "zoom": 1.0,
                "pan_x": 0.0,
                "pan_y": 0.0,
                "last_image_hash": None,
                "last_fit_mode": fit_mode,
                "last_white_matte": white_matte
            }
            try:
                t = Thread(target=self._window_loop, args=(display_idx, res_w, res_h, fit_mode), daemon=True)
                self._windows[display_idx]["thread"] = t
                t.start()
                print(f"Preview Monitor: Window thread started for monitor {display_idx}")
            except Exception as e:
                print(f"Preview Monitor: Error creating window on monitor {display_idx}: {e}")
                del self._windows[display_idx]
        else:
            # Update the images and ensure window is visible
            with self._windows[display_idx]["lock"]:
                self._windows[display_idx]["images"] = pil_images
                self._windows[display_idx]["compare_image"] = compare_pil_images
                self._windows[display_idx]["display_mode"] = display_mode
                self._windows[display_idx]["visible"] = True
                self._windows[display_idx]["fit_mode"] = fit_mode
                self._windows[display_idx]["res_w"] = res_w
                self._windows[display_idx]["res_h"] = res_h
                self._windows[display_idx]["gain"] = gain
                self._windows[display_idx]["gamma"] = gamma
                self._windows[display_idx]["saturation"] = saturation
                self._windows[display_idx]["white_matte"] = white_matte
                self._windows[display_idx]["fps_mode"] = fps_mode

        return (images,)

    def _apply_image_adjustments(self, pil_img, gain, gamma, saturation):
        """Apply gain, gamma, and saturation adjustments to the image"""
        if gain == 1.0 and gamma == 1.0 and saturation == 1.0:
            return pil_img  # No adjustments needed
            
        img_array = np.array(pil_img).astype(np.float32) / 255.0
        
        # Apply gain (exposure)
        if gain != 1.0:
            img_array = img_array * gain
        
        # Apply gamma
        if gamma != 1.0:
            img_array = np.power(img_array, 1.0 / gamma)
        
        # Apply saturation
        if saturation != 1.0:
            # Convert to HSV for saturation control
            hsv_img = np.array(pil_img.convert('HSV')).astype(np.float32) / 255.0
            # Adjust saturation channel
            hsv_img[:, :, 1] = np.clip(hsv_img[:, :, 1] * saturation, 0.0, 1.0)
            # Convert back to RGB
            hsv_img = (hsv_img * 255.0).astype(np.uint8)
            img_array = np.array(Image.fromarray(hsv_img, 'HSV').convert('RGB')).astype(np.float32) / 255.0
        
        # Clip and convert back to uint8
        img_array = np.clip(img_array, 0.0, 1.0) * 255.0
        return Image.fromarray(img_array.astype(np.uint8))

    def _create_comparison_image(self, img1, img2, target_w, target_h, mode, white_matte=False, mouse_x=None, zoom=1.0, pan_x=0.0, pan_y=0.0):
        """Create an overlay comparison image with mouse-controlled split"""
        if img1 is None or img2 is None:
            return self._scale_image(img1 or img2, target_w, target_h, mode, white_matte, zoom, pan_x, pan_y)
        
        # Scale both images to full size
        scaled_img1 = self._scale_image(img1, target_w, target_h, mode, white_matte, zoom, pan_x, pan_y)
        scaled_img2 = self._scale_image(img2, target_w, target_h, mode, white_matte, zoom, pan_x, pan_y)
        
        # If no mouse position, show side-by-side as fallback
        if mouse_x is None:
            mouse_x = target_w // 2
        
        # Ensure mouse_x is within bounds
        mouse_x = max(0, min(mouse_x, target_w))
        
        # Convert to numpy arrays for faster processing
        img1_array = np.array(scaled_img1)
        img2_array = np.array(scaled_img2)
        
        # Create result array more efficiently
        # Create a mask for the split line
        mask = np.arange(target_w)[None, :] < mouse_x
        # Expand mask to match image dimensions
        mask_3d = np.stack([mask] * target_h, axis=0)
        if len(img1_array.shape) == 3:
            mask_3d = np.stack([mask_3d] * img1_array.shape[2], axis=2)
        
        result_array = np.where(mask_3d, img1_array, img2_array)
        
        # Add vertical line more efficiently
        line_color = (255, 255, 0) if not white_matte else (0, 0, 0)
        if 0 < mouse_x < target_w:
            result_array[:, mouse_x-1:mouse_x+1] = line_color
        
        return Image.fromarray(result_array)

    def _scale_image(self, pil_img, target_w, target_h, mode, white_matte=False, zoom=1.0, pan_x=0.0, pan_y=0.0):
        img_w, img_h = pil_img.size
        # Use white background if white_matte is enabled, otherwise black
        bg_color = (255, 255, 255) if white_matte else (0, 0, 0)
        canvas = Image.new("RGB", (target_w, target_h), bg_color)

        if mode == "none" or mode == "center":
            # Keep original size, center it
            x = (target_w - img_w) // 2
            y = (target_h - img_h) // 2
            canvas.paste(pil_img, (x, y))
            return canvas

        if mode == "width":
            scale = target_w / img_w
            new_w = target_w
            new_h = int(img_h * scale)
        elif mode == "height":
            scale = target_h / img_h
            new_h = target_h
            new_w = int(img_w * scale)
        elif mode == "fit":
            scale = min(target_w / img_w, target_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
        elif mode == "fill":
            scale = max(target_w / img_w, target_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
        elif mode == "distort":
            new_w, new_h = target_w, target_h
        else:
            new_w, new_h = img_w, img_h

        # Apply zoom
        if zoom != 1.0:
            new_w = int(new_w * zoom)
            new_h = int(new_h * zoom)

        resized = pil_img.resize((new_w, new_h), Image.LANCZOS)
        
        # Calculate position with pan offset
        x = (target_w - new_w) // 2 + int(pan_x)
        y = (target_h - new_h) // 2 + int(pan_y)
        
        # Only paste if the image is visible within the canvas
        if x < target_w and y < target_h and x + new_w > 0 and y + new_h > 0:
            # Crop the image if it goes outside the canvas
            crop_x1 = max(0, -x)
            crop_y1 = max(0, -y)
            crop_x2 = min(new_w, target_w - x)
            crop_y2 = min(new_h, target_h - y)
            
            if crop_x2 > crop_x1 and crop_y2 > crop_y1:
                cropped = resized.crop((crop_x1, crop_y1, crop_x2, crop_y2))
                paste_x = max(0, x)
                paste_y = max(0, y)
                canvas.paste(cropped, (paste_x, paste_y))
        
        return canvas

    def _get_image_hash(self, pil_img):
        """Generate a hash for the image to detect changes"""
        if pil_img is None:
            return None
        return hashlib.md5(pil_img.tobytes()).hexdigest()

    def _window_loop(self, display_idx, res_w, res_h, fit_mode):
        # Ensure pygame is initialized
        if PYGAME_AVAILABLE and not pygame.get_init():
            pygame.init()
        
        # Determine monitor position
        if SCREENINFO_AVAILABLE:
            try:
                monitor_info = get_monitors()[display_idx]
                mon_x, mon_y = monitor_info.x, monitor_info.y
            except Exception:
                mon_x, mon_y = 0, 0
        else:
            mon_x, mon_y = 0, 0

        try:
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{mon_x},{mon_y}"
            screen = pygame.display.set_mode((res_w, res_h), pygame.NOFRAME)
            pygame.display.set_caption(f"Preview Monitor {display_idx} - Press H for help")
            pygame.mouse.set_visible(True)  # Show mouse cursor
            clock = pygame.time.Clock()
            print(f"Preview Monitor: Window created successfully on monitor {display_idx} at position ({mon_x}, {mon_y})")
        except Exception as e:
            print(f"Preview Monitor: Error creating window on monitor {display_idx}: {e}")
            return

        # Initialize last rendered image
        last_rendered_image = None
        running = True
        mouse_x = res_w // 2  # Default split position
        dragging = False
        last_mouse_pos = (0, 0)
        last_mouse_update = 0  # For mouse movement throttling
        mouse_throttle_interval = 16  # ~60fps for mouse updates (16ms)
        frame_count = 0
        last_fps_time = 0
        performance_mode = "normal"  # normal, high_performance, low_latency
        
        while running:
            # Check if we should continue running
            with self._windows[display_idx]["lock"]:
                if not self._windows[display_idx]["running"]:
                    running = False
                    break
                images = self._windows[display_idx]["images"]
                current_idx = self._windows[display_idx]["current_idx"]
                display_mode = self._windows[display_idx]["display_mode"]
                compare_image = self._windows[display_idx]["compare_image"]
                visible = self._windows[display_idx]["visible"]
                current_fit_mode = self._windows[display_idx].get("fit_mode", fit_mode)
                res_w = self._windows[display_idx].get("res_w", res_w)
                res_h = self._windows[display_idx].get("res_h", res_h)
                white_matte = self._windows[display_idx].get("white_matte", False)
                fps_mode = self._windows[display_idx].get("fps_mode", "smart")
                zoom = self._windows[display_idx].get("zoom", 1.0)
                pan_x = self._windows[display_idx].get("pan_x", 0.0)
                pan_y = self._windows[display_idx].get("pan_y", 0.0)

            if not running:
                break

            # Determine target FPS based on mode
            if fps_mode == "15fps":
                target_fps = 15
            elif fps_mode == "30fps":
                target_fps = 30
            else:  # smart mode
                target_fps = 30  # Base rate, but smart rendering will skip work

            # Fill background
            bg_color = (255, 255, 255) if white_matte else (0, 0, 0)
            screen.fill(bg_color)

            needs_redraw = False
            if visible and images and len(images) > 0:
                if fps_mode == "smart":
                    # Smart mode: Only redraw if something changed
                    current_image = images[current_idx] if current_idx < len(images) else images[0]
                    current_image_hash = self._get_image_hash(current_image)
                    current_settings_hash = f"{current_fit_mode}_{white_matte}_{display_mode}_{current_idx}"
                    
                    # Check if we need to redraw
                    settings_changed = (current_fit_mode != self._windows[display_idx].get("last_fit_mode") or
                                      white_matte != self._windows[display_idx].get("last_white_matte") or
                                      display_mode != self._windows[display_idx].get("last_display_mode") or
                                      current_idx != self._windows[display_idx].get("last_current_idx") or
                                      zoom != self._windows[display_idx].get("last_zoom", 1.0) or
                                      pan_x != self._windows[display_idx].get("last_pan_x", 0.0) or
                                      pan_y != self._windows[display_idx].get("last_pan_y", 0.0))
                    image_changed = (current_image_hash != self._windows[display_idx].get("last_image_hash"))
                    
                    # In comparison mode, only redraw when necessary for better performance
                    if display_mode == "comparison":
                        # Only redraw if image, settings, or mouse position changed
                        mouse_changed = (mouse_x != self._windows[display_idx].get("last_mouse_x", res_w // 2))
                        needs_redraw = image_changed or settings_changed or mouse_changed
                    else:
                        needs_redraw = image_changed or settings_changed
                    
                    # Update last known state
                    with self._windows[display_idx]["lock"]:
                        self._windows[display_idx]["last_image_hash"] = current_image_hash
                        self._windows[display_idx]["last_fit_mode"] = current_fit_mode
                        self._windows[display_idx]["last_white_matte"] = white_matte
                        self._windows[display_idx]["last_display_mode"] = display_mode
                        self._windows[display_idx]["last_current_idx"] = current_idx
                        self._windows[display_idx]["last_zoom"] = zoom
                        self._windows[display_idx]["last_pan_x"] = pan_x
                        self._windows[display_idx]["last_pan_y"] = pan_y
                        self._windows[display_idx]["last_mouse_x"] = mouse_x
                else:
                    # Fixed FPS modes: Always redraw
                    needs_redraw = True

                if needs_redraw:
                    current_image = images[current_idx] if current_idx < len(images) else images[0]
                    
                    if display_mode == "comparison" and compare_image and len(compare_image) > 0:
                        compare_image = compare_image[current_idx] if current_idx < len(compare_image) else compare_image[0]
                        scaled_img = self._create_comparison_image(current_image, compare_image, res_w, res_h, current_fit_mode, white_matte, mouse_x, zoom, pan_x, pan_y)
                    else:
                        # If comparison mode is selected but no compare images available, fall back to single mode
                        if display_mode == "comparison":
                            print("Preview Monitor: Comparison mode selected but no compare images available, falling back to single mode")
                        scaled_img = self._scale_image(current_image, res_w, res_h, current_fit_mode, white_matte, zoom, pan_x, pan_y)
                    
                    if scaled_img.mode not in ("RGB", "RGBA"):
                        scaled_img = scaled_img.convert("RGB")
                    last_rendered_image = pygame.image.fromstring(scaled_img.tobytes(), scaled_img.size, scaled_img.mode)
                    
                    # Update window title with current status
                    status_text = f"Preview Monitor {display_idx} - {display_mode.title()}"
                    if display_mode == "slideshow" and len(images) > 1:
                        status_text += f" ({current_idx + 1}/{len(images)})"
                    if display_mode == "comparison" and compare_image and len(compare_image) > 0:
                        status_text += f" - Comparison (Split: {mouse_x}px)"
                    if zoom != 1.0:
                        status_text += f" - Zoom: {zoom:.1f}x"
                    status_text += " - Press H for help"
                    pygame.display.set_caption(status_text)

                # Always blit the last rendered image if available
                if last_rendered_image:
                    x_pos = (res_w - last_rendered_image.get_width()) // 2
                    y_pos = (res_h - last_rendered_image.get_height()) // 2
                    screen.blit(last_rendered_image, (x_pos, y_pos))

            pygame.display.flip()

            # Handle events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    with self._windows[display_idx]["lock"]:
                        self._windows[display_idx]["visible"] = False
                elif event.type == pygame.MOUSEMOTION:
                    current_time = pygame.time.get_ticks()
                    
                    # Throttle mouse updates for better performance
                    if current_time - last_mouse_update > mouse_throttle_interval:
                        # Update mouse position for comparison mode
                        if display_mode == "comparison":
                            mouse_x = event.pos[0]
                            # Force redraw when mouse moves in comparison mode
                            needs_redraw = True
                        
                        # Handle panning when dragging
                        if dragging:
                            dx = event.pos[0] - last_mouse_pos[0]
                            dy = event.pos[1] - last_mouse_pos[1]
                            with self._windows[display_idx]["lock"]:
                                self._windows[display_idx]["pan_x"] += dx
                                self._windows[display_idx]["pan_y"] += dy
                            needs_redraw = True
                        
                        last_mouse_pos = event.pos
                        last_mouse_update = current_time
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:  # Left mouse button
                        dragging = True
                        last_mouse_pos = event.pos
                elif event.type == pygame.MOUSEBUTTONUP:
                    if event.button == 1:  # Left mouse button
                        dragging = False
                elif event.type == pygame.MOUSEWHEEL:
                    # Handle zoom with mouse wheel
                    zoom_factor = 1.1 if event.y > 0 else 0.9
                    with self._windows[display_idx]["lock"]:
                        new_zoom = self._windows[display_idx]["zoom"] * zoom_factor
                        # Limit zoom range
                        new_zoom = max(0.1, min(10.0, new_zoom))
                        self._windows[display_idx]["zoom"] = new_zoom
                    needs_redraw = True
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        with self._windows[display_idx]["lock"]:
                            self._windows[display_idx]["visible"] = False
                    elif event.key == pygame.K_LEFT and display_mode == "slideshow":
                        # Previous image
                        with self._windows[display_idx]["lock"]:
                            if len(images) > 1:
                                self._windows[display_idx]["current_idx"] = (current_idx - 1) % len(images)
                                print(f"Preview Monitor: Showing image {self._windows[display_idx]['current_idx'] + 1}/{len(images)}")
                    elif event.key == pygame.K_RIGHT and display_mode == "slideshow":
                        # Next image
                        with self._windows[display_idx]["lock"]:
                            if len(images) > 1:
                                self._windows[display_idx]["current_idx"] = (current_idx + 1) % len(images)
                                print(f"Preview Monitor: Showing image {self._windows[display_idx]['current_idx'] + 1}/{len(images)}")
                    elif event.key == pygame.K_c:
                        # Toggle comparison mode
                        with self._windows[display_idx]["lock"]:
                            if self._windows[display_idx]["display_mode"] == "comparison":
                                self._windows[display_idx]["display_mode"] = "single"
                                print("Preview Monitor: Switched to single image mode")
                            else:
                                self._windows[display_idx]["display_mode"] = "comparison"
                                print("Preview Monitor: Switched to comparison mode")
                    elif event.key == pygame.K_s:
                        # Toggle slideshow mode
                        with self._windows[display_idx]["lock"]:
                            if self._windows[display_idx]["display_mode"] == "slideshow":
                                self._windows[display_idx]["display_mode"] = "single"
                                print("Preview Monitor: Switched to single image mode")
                            else:
                                self._windows[display_idx]["display_mode"] = "slideshow"
                                print("Preview Monitor: Switched to slideshow mode")
                    elif event.key == pygame.K_r:
                        # Reset zoom and pan
                        with self._windows[display_idx]["lock"]:
                            self._windows[display_idx]["zoom"] = 1.0
                            self._windows[display_idx]["pan_x"] = 0.0
                            self._windows[display_idx]["pan_y"] = 0.0
                        needs_redraw = True
                        print("Preview Monitor: Reset zoom and pan")
                    elif event.key == pygame.K_PLUS or event.key == pygame.K_EQUALS:
                        # Zoom in
                        with self._windows[display_idx]["lock"]:
                            new_zoom = min(10.0, self._windows[display_idx]["zoom"] * 1.2)
                            self._windows[display_idx]["zoom"] = new_zoom
                        needs_redraw = True
                    elif event.key == pygame.K_MINUS:
                        # Zoom out
                        with self._windows[display_idx]["lock"]:
                            new_zoom = max(0.1, self._windows[display_idx]["zoom"] * 0.8)
                            self._windows[display_idx]["zoom"] = new_zoom
                        needs_redraw = True
                    elif event.key == pygame.K_m:
                        # Toggle mouse visibility
                        current_visible = pygame.mouse.get_visible()
                        pygame.mouse.set_visible(not current_visible)
                        print(f"Preview Monitor: Mouse cursor {'shown' if not current_visible else 'hidden'}")
                    elif event.key == pygame.K_q:
                        # Force cleanup all windows
                        self.cleanup_all_windows()
                        print("Preview Monitor: All windows cleaned up")
                    elif event.key == pygame.K_h:
                        # Show help
                        print("Preview Monitor Controls:")
                        print("  ESC - Hide window")
                        print("  S - Toggle slideshow mode")
                        print("  C - Toggle comparison mode")
                        print("  Left/Right Arrow - Navigate images (slideshow mode)")
                        print("  Mouse Movement - Control split line in comparison mode")
                        print("  Mouse Wheel - Zoom in/out")
                        print("  Left Mouse Drag - Pan image")
                        print("  +/- - Zoom in/out")
                        print("  R - Reset zoom and pan")
                        print("  M - Toggle mouse cursor visibility")
                        print("  Q - Force cleanup all windows")
                        print("  H - Show this help")
                        print("")
                        print("Comparison Mode:")
                        print("  - Two images are overlaid")
                        print("  - Move mouse left/right to control split line")
                        print("  - Left side shows top image, right side shows bottom image")
                        print("")
                        print("Zoom and Pan:")
                        print("  - Mouse wheel: zoom in/out")
                        print("  - Left mouse drag: pan image")
                        print("  - +/- keys: zoom in/out")
                        print("  - R key: reset zoom and pan")

            # Performance monitoring and adaptive throttling
            frame_count += 1
            current_time = pygame.time.get_ticks()
            if current_time - last_fps_time > 1000:  # Update FPS every second
                actual_fps = frame_count * 1000 / (current_time - last_fps_time)
                
                # Adaptive performance adjustment
                if actual_fps < target_fps * 0.7:  # If FPS is significantly lower than target
                    if performance_mode != "high_performance":
                        performance_mode = "high_performance"
                        mouse_throttle_interval = 33  # ~30fps for mouse updates
                        print(f"Preview Monitor: Switched to high performance mode (FPS: {actual_fps:.1f})")
                elif actual_fps > target_fps * 0.9:  # If FPS is good
                    if performance_mode != "normal":
                        performance_mode = "normal"
                        mouse_throttle_interval = 16  # ~60fps for mouse updates
                        print(f"Preview Monitor: Switched to normal mode (FPS: {actual_fps:.1f})")
                
                frame_count = 0
                last_fps_time = current_time
            
            clock.tick(target_fps)

        # Clean up pygame resources for this window
        try:
            pygame.display.quit()
            print(f"Preview Monitor: Window on monitor {display_idx} cleaned up")
        except Exception as e:
            print(f"Preview Monitor: Error cleaning up window on monitor {display_idx}: {e}")


# Node registration
NODE_CLASS_MAPPINGS = {"PreviewImageMonitor": PreviewImageMonitor}
NODE_DISPLAY_NAME_MAPPINGS = {"PreviewImageMonitor": "🖥️ Preview Image Monitor"}