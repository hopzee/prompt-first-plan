"""Business services. Every public function receives the acting ``CurrentUser``
and enforces roles and record ownership itself, so the rules hold no matter
which page (or script) calls it."""
