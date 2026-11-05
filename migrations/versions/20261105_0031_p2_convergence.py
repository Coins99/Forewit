"""Converge the published Notes and workspace migration histories.

The environment adopts their identical column operation through migration_forks;
both original revision files remain immutable for profiles that have used them.
"""

revision: str = "20261105_0031"
down_revision: tuple[str, str] = ("20261104_0029", "20261103_0030")
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
