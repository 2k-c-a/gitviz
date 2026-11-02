// The activity page was merged into the overview; keep old links (repo, snapshot, view state) working.
location.replace(new URL(`overview.html${location.search}${location.hash}`, location.href).href);
