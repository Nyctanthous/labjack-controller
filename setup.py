from setuptools import setup

setup(name='labjackcontroller',
      description='A helper library to control LabJack devices',
      version='0.2',
      url='https://github.com/Nyctanthous/labjack-controller',
      author='Ben Montgomery',
      packages=['labjackcontroller'],
      license='MIT',
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: MIT License',
          'Programming Language :: Python :: 3'
      ],
      install_requires=[
                        'typing',
                        'numpy',
                        'pandas',
                        'colorama',
                        'LJMPython'
                       ],
      zip_safe=False)
