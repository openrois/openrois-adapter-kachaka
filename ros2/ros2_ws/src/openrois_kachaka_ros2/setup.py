from setuptools import find_packages, setup

package_name = "openrois_kachaka_ros2"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["tests"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Coarobo",
    maintainer_email="info@coarobo.com",
    description="OpenRoIS adapter for the Kachaka robot using ROS 2",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "kachaka_adapter = openrois_kachaka_ros2.kachaka_ros2_adapter:main",
        ],
    },
)
