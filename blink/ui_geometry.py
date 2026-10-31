# UI geometry helper
def calculate_corner_radius(width, height, factor=0.18):
    return int(min(width, height) * factor)
