"""
Install the soofi-xyz team kit as Claude Code agents and skills.

    python tools/sync_team_kit.py            # the subset relevant to this repo
    python tools/sync_team_kit.py --all      # all 34 agents and ~50 skills

The kit ships Codex, Cursor and Copilot packaging but no Claude Code plugin
manifest, so this translates rather than symlinks. Two things have to change:

  * `model:` carries OpenAI ids (gpt-5.4-high). Claude Code expects one of
    opus/sonnet/haiku, so *-high maps to opus and *-medium to sonnet — the
    intent survives, the identifier does not.
  * Agents cite skills as `skills/<name>/`. Installed under `.claude/`, that
    path no longer resolves, so the references are rewritten to match.

Everything installed is upstream content. Re-run to pull changes; local edits
under .claude/agents or .claude/skills will be overwritten.
"""
import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = "https://github.com/soofi-xyz/soofi-xyz-team-kit.git"
ROOT = pathlib.Path(__file__).parent.parent
MODEL_MAP = {"high": "opus", "medium": "sonnet", "low": "haiku"}

# Curated for this repo. The rest of the kit is Elephant/SOCAPITAL-specific:
# Asana pipelines, communication runtimes, property data, marketplace tenancy.
RELEVANT_AGENTS = [
    "arceus",     # router — picks the right specialist
    "espeon",     # end-to-end RAG (the embeddings work)
    "alakazam",   # reusable AWS RAG
    "ash",        # AI-agent builder
    "metagross",  # fullstack monorepo architect
    "audino",     # frontend bug-fix
    "smeargle",   # responsive design tests (Playwright)
    "sylveon",    # Figma to code
]
RELEVANT_SKILLS = [
    "apply-engineering-guidelines",
    "build-ai-agents",
    "build-rag-systems",
    "build-frontend-backends",
    "integrate-ci-cd",
    "responsive-design-tests",
    "frontend-bug-fix",
    "babysit-release",
]


def translate_agent(text):
    def fix_model(m):
        tier = m.group(1).rsplit("-", 1)[-1]
        return f"model: {MODEL_MAP.get(tier, 'opus')}"
    text = re.sub(r"^model:\s*(\S+)\s*$", fix_model, text, flags=re.M)
    return text.replace("`skills/", "`.claude/skills/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="install the entire kit")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        print(f"→ cloning {REPO}")
        subprocess.run(["git", "clone", "--depth", "1", "-q", REPO, tmp], check=True)
        src = pathlib.Path(tmp)
        version = re.search(r'"version":\s*"([^"]+)"',
                            (src / "plugin.json").read_text()).group(1)

        agents_out = ROOT / ".claude" / "agents"
        skills_out = ROOT / ".claude" / "skills"
        for d in (agents_out, skills_out):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

        agents = sorted(src.glob("agents/*.md"))
        skills = sorted(p for p in (src / "skills").iterdir() if p.is_dir())
        if not args.all:
            agents = [a for a in agents if a.stem in RELEVANT_AGENTS]
            skills = [s for s in skills if s.name in RELEVANT_SKILLS]

        for a in agents:
            (agents_out / a.name).write_text(translate_agent(a.read_text()))
        for s in skills:
            shutil.copytree(s, skills_out / s.name)

        (ROOT / ".claude" / "TEAM_KIT_VERSION").write_text(
            f"soofi-xyz-team-kit {version}\n"
            f"{'full kit' if args.all else 'curated subset'}: "
            f"{len(agents)} agents, {len(skills)} skills\n")

        print(f"→ {len(agents)} agents → .claude/agents/")
        print(f"→ {len(skills)} skills → .claude/skills/")
        print(f"→ kit version {version}")
        if not args.all:
            print("   (--all installs the remaining Elephant/SOCAPITAL specialists)")


if __name__ == "__main__":
    sys.exit(main())
