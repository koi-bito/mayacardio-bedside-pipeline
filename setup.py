from setuptools import setup, find_packages

setup(
    name="mayacardio-pipeline",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "httpx>=0.25.0",
        "pydantic>=2.0.0",
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "pytest>=7.0.0",
    ],
    entry_points={
        "console_scripts": [
            "pipeline=pipeline.cli:main",
            "biosignal-mock=mock.biosignal_mock:main",
            "check-ground-truth=mock.check_ground_truth:main",
        ],
    },
)
