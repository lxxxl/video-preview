from core.torrent_parser import validate_magnet

DEFAULT_SAMPLE_POINTS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95]


def validate_task_request(data: dict) -> tuple[dict | None, str | None]:
    if not data or not isinstance(data, dict):
        return None, "Request body must be JSON"

    magnet = data.get("magnet")
    if not magnet or not isinstance(magnet, str):
        return None, "Missing or invalid 'magnet' field"

    if not validate_magnet(magnet):
        return None, "Invalid magnet URI format (must contain xt=urn:btih: with valid info_hash)"

    sample_points = data.get("sample_points", DEFAULT_SAMPLE_POINTS)
    if not isinstance(sample_points, list):
        return None, "'sample_points' must be a list"
    if len(sample_points) > 19:
        return None, "'sample_points' max length is 19"
    for p in sample_points:
        if not isinstance(p, int) or not 1 <= p <= 99:
            return None, "Each sample point must be an integer between 1 and 99"

    timeout = data.get("timeout", 600)
    if not isinstance(timeout, int) or not 60 <= timeout <= 600:
        return None, "'timeout' must be an integer between 60 and 600"

    return {
        "magnet": magnet,
        "sample_points": sorted(set(sample_points)),
        "timeout": timeout,
    }, None
