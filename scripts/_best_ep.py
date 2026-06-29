import re
rows = re.findall(r'ep(\d+) \| \*\*(\d+)%', open('results_rk_gated_seed42_strict.md').read())
print(max(rows, key=lambda x: int(x[1]))[0] if rows else 150)
