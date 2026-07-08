rem =========================================
rem reset all environment variabls
rem =========================================

@echo off
setlocal

echo "keep basic env"
set SystemRoot=%SystemRoot%
set ComSpec=%ComSpec%
set TEMP=%TEMP%
set TMP=%TMP%

echo "clear all env set" 
for %%V in (
  PATH INCLUDE LIB LIBPATH
  CC CXX CPP CFLAGS CXXFLAGS LDFLAGS
  MAKE MAKEFLAGS MFLAGS
  SHELL HOME TERM
  CYGWIN CHERE_INVOKING
  MSYSTEM MINGW_PREFIX
  PKG_CONFIG_PATH PKG_CONFIG_LIBDIR
  CMAKE_GENERATOR
) do set %%V=

set PATH=%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem;C:\Strawberry\perl\bin;c:\Users\antman\.cargo\bin

call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"

set LIBCLANG_PATH=C:\Program Files\LLVM\bin

echo "dump env"
set

@echo %PATH%

cargo build --release

