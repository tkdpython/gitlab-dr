from setuptools import find_packages, setup


setup(
    name="gitlab-dr",
    version="0.1.0",
    description="GitLab disaster recovery backup/restore CLI",
    long_description="Backup and restore GitLab groups/projects to zip archives.",
    long_description_content_type="text/plain",
    packages=find_packages(exclude=("tests",)),
    include_package_data=True,
    python_requires=">=3.6",
    install_requires=[
        "requests>=2.27.1,<3",
        "pyzipper>=0.3.6,<1",
    ],
    entry_points={
        "console_scripts": [
            "gitlab_dr=gitlab_dr.cli:main",
        ]
    },
)
