# Models

This directory hosts ML weights used by Maniac.

## `*.bin` / `*.json` (root)

Human Library face/pose models — see
[List of Models & Credits](https://github.com/vladmandic/human/wiki/Models).

## `ml/` (auto-managed by `python/analyze.py`)

Lazy-downloaded on first analysis run. Safe to delete — they will be
re-downloaded the next time their detector is invoked.

| File                              | Detector                  | Source                                                            | Size  |
|-----------------------------------|---------------------------|-------------------------------------------------------------------|-------|
| `yolov8n.pt`                      | YOLOv8 (object/animal)    | `github.com/ultralytics/assets/releases/download/v8.3.0`          | ~6 MB |
| `resnet18_places365.pth.tar`      | Places365 ResNet18 (place)| `places2.csail.mit.edu/models_places365`                          | ~46 MB|
| `categories_places365.txt`        | Places365 labels          | `github.com/csailvision/places365`                                | ~6 KB |
| `detect_cache.sqlite`             | Per-file detection cache  | local                                                             | varies|

The cache is keyed by `(absolute path, detector, mtime, size)` so editing or
re-encoding a file invalidates its entries automatically.
