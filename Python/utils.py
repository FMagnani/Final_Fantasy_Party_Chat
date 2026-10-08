import json


def _flatten(node, prefix=""):
    """Flattens nested dicts/lists into {"dotted.path": leaf_value}.

    Lists are indexed by position: party.1.hp.current, party.0.magic.current_mp.0
    """
    if isinstance(node, dict):
        items = node.items()
    elif isinstance(node, list):
        items = enumerate(node)
    else:
        return {prefix: node}

    flat = {}
    for key, value in items:
        path = f"{prefix}.{key}" if prefix else str(key)
        flat.update(_flatten(value, path))
    return flat


def diff_json(old_data, new_data, ignore=()):
    """Same as diff_json_files, but on already-parsed JSON data."""
    old, new = _flatten(old_data), _flatten(new_data)

    ignore = tuple(ignore)
    changes = {}
    for key in old.keys() | new.keys():
        if ignore and any(key == p or key.startswith(p + ".") for p in ignore):
            continue
        old_value, new_value = old.get(key), new.get(key)
        if key not in old or key not in new or old_value != new_value:
            changes[key] = {"old_value": old_value, "new_value": new_value}

    return dict(sorted(changes.items()))


def diff_json_files(old_path, new_path, output_path=None, ignore=()):
    """Compares two JSON files and returns only the entries that changed.

    Result: {"party.1.hp.current": {"old_value": 33, "new_value": 2}, ...}
      * the key is the dotted path of the changed entry
      * an entry that exists only in the new file has old_value None,
        one that exists only in the old file has new_value None
    ignore: iterable of path prefixes to skip, e.g. ignore=("frame",)
    If output_path is given, the result is also written there as JSON.
    """
    with open(old_path, "r", encoding="utf-8") as f:
        old = json.load(f)
    with open(new_path, "r", encoding="utf-8") as f:
        new = json.load(f)
    changes = diff_json(old, new, ignore)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(changes, f, indent=4)
    return changes


if __name__ == "__main__":
    print(json.dumps(
        diff_json_files("ff1_game_state_old.json", "ff1_game_state_new.json"), indent=4))
