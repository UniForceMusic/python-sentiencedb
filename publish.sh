#!/usr/bin/env bash
# Check, build and publish sentiencedb to PyPI, then tag the release.
#
# Usage: ./publish.sh [--test] [--dry-run] [--yes]
#   --test     upload to TestPyPI instead of PyPI (token: TEST_PYPI_API_KEY); no git tag
#   --dry-run  run the checks and build, but do not upload or tag (a dirty working tree is allowed)
#   --yes      do not ask before uploading and pushing the tag
#
# The API token is read from PYPI_API_KEY (TEST_PYPI_API_KEY with --test), from the environment or from .env.
# The version comes from pyproject.toml and needs a "## <version>" section in CHANGELOG.md.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON=.venv/bin/python
PACKAGE=sentiencedb

index_url=https://pypi.org
upload_url=https://upload.pypi.org/legacy/
token_var=PYPI_API_KEY
test_pypi=0
dry_run=0
assume_yes=0

for arg in "$@"; do
    case "$arg" in
        --test)
            test_pypi=1
            index_url=https://test.pypi.org
            upload_url=https://test.pypi.org/legacy/
            token_var=TEST_PYPI_API_KEY
            ;;
        --dry-run) dry_run=1 ;;
        -y | --yes) assume_yes=1 ;;
        -h | --help)
            sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "error: unknown argument: $arg (see --help)" >&2
            exit 2
            ;;
    esac
done

die() {
    echo "error: $*" >&2
    exit 1
}

step() {
    printf '\n==> %s\n' "$*"
}

confirm() {
    [ "$assume_yes" = 1 ] && return 0
    local answer
    read -r -p "$1 [y/N] " answer
    [ "$answer" = y ] || [ "$answer" = Y ]
}

[ -x "$PYTHON" ] || die "no virtualenv; run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
"$PYTHON" -c 'import build, twine' 2>/dev/null || die "build/twine missing; run: .venv/bin/pip install -r requirements.txt"

version=$("$PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])') ||
    die "could not read the version from pyproject.toml"
tag="v$version"

step "Releasing $PACKAGE $version to $index_url"

grep -qx "## $version" CHANGELOG.md || die "CHANGELOG.md has no '## $version' section"

if [ "$dry_run" = 0 ]; then
    [ -z "$(git status --porcelain)" ] || die "working tree is not clean; commit or stash first (or use --dry-run)"
    branch=$(git rev-parse --abbrev-ref HEAD)
    [ "$branch" = main ] || die "not on main (on $branch)"
    if [ "$test_pypi" = 0 ] && git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
        die "tag $tag already exists; bump the version in pyproject.toml first"
    fi

    token="${!token_var:-}"
    if [ -z "$token" ] && [ -f .env ]; then
        token=$(sed -n "s/^$token_var=//p" .env | tail -n 1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//')
    fi
    [ -n "$token" ] || die "$token_var is not set (environment or .env)"
fi

status=$(curl -s -o /dev/null -w '%{http_code}' "$index_url/pypi/$PACKAGE/$version/json" || true)
[ "$status" != 200 ] || die "$PACKAGE $version is already on $index_url; bump the version in pyproject.toml first"

step "Lint, format and type check"
"$PYTHON" -m ruff check src/$PACKAGE tests
"$PYTHON" -m ruff format --check src/$PACKAGE tests
"$PYTHON" -m mypy src/$PACKAGE

step "Tests"
"$PYTHON" -m pytest -q

step "Build"
rm -rf dist build src/*.egg-info
"$PYTHON" -m build
"$PYTHON" -m twine check --strict dist/*

wheel="dist/$PACKAGE-$version-py3-none-any.whl"
sdist="dist/$PACKAGE-$version.tar.gz"
[ -f "$wheel" ] && [ -f "$sdist" ] || die "expected $wheel and $sdist, got: $(ls dist)"

step "Smoke test the wheel in a clean virtualenv"
smoke_dir=$(mktemp -d)
trap 'rm -rf "$smoke_dir"' EXIT
"$PYTHON" -m venv "$smoke_dir/venv"
"$smoke_dir/venv/bin/pip" install -q --disable-pip-version-check "$wheel"
(cd "$smoke_dir" && "$smoke_dir/venv/bin/python" - "$version" <<'EOF'
import sys
from importlib.resources import files

import sentiencedb
from sentiencedb import connect_sqlite

assert sentiencedb.__version__ == sys.argv[1], sentiencedb.__version__
assert files("sentiencedb").joinpath("py.typed").is_file(), "py.typed missing from the wheel"
db = connect_sqlite(":memory:")
assert db.query("SELECT 1 AS one").fetch_dicts() == [{"one": 1}]
print(f"sentiencedb {sentiencedb.__version__} imports and runs from the wheel")
EOF
)

if [ "$dry_run" = 1 ]; then
    step "Dry run: built $(ls dist | tr '\n' ' '), nothing uploaded"
    exit 0
fi

confirm "Upload $PACKAGE $version to $index_url?" || die "aborted"

step "Upload"
TWINE_USERNAME=__token__ TWINE_PASSWORD="$token" "$PYTHON" -m twine upload --non-interactive \
    --repository-url "$upload_url" "$wheel" "$sdist"

if [ "$test_pypi" = 1 ]; then
    step "Published to TestPyPI: $index_url/project/$PACKAGE/$version/"
    echo "Try it: pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ $PACKAGE==$version"
    exit 0
fi

step "Tag $tag"
git tag -a "$tag" -m "$PACKAGE $version"
if confirm "Push $tag to origin?"; then
    git push origin "$tag"
else
    echo "Tag created locally; push it later with: git push origin $tag"
fi

step "Published: $index_url/project/$PACKAGE/$version/"
