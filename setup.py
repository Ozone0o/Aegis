from setuptools import find_packages, setup

package_name = "aegis"

setup(
    name=package_name,
    version="0.2.0",
    packages=find_packages("src"),
    package_dir={"": "src"},
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            [f"resource/{package_name}"],
        ),
        (f"share/{package_name}", ["package.xml"]),
    ],
    install_requires=["setuptools"],
    entry_points={"console_scripts": ["aegis = aegis.cli:main"]},
)
