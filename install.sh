#!/bin/bash

print_help() {
    echo "\
OVERVIEW: BinSync server & client installer

USAGE: install.sh [options]

OPTIONS:

Decompiler Install Options:
  -i --ida PATH                    - Install for IDA where PATH should be the PATH to the 'plugins' folder in your IDA install.
  -b --binja PATH                  - Install for Binary Ninja where PATH should be the PATH to the 'plugins' folder in your Binary Ninja install.
  -a --angr PATH                   - Install for angr (angr-management), where PATH should be the PATH to the 'plugins' folder in your angr-management install.

  For help finding your 'plugins' folder, see the docs for advice.

Generic Options:
  --link                           - Link files with ln instead of copying them. Do this only if you plan on keeping the BinSync GitHub repo in the
                                     the same place for a while. Useful if you update BinSync frequently, since you will only need to pull.
  --no-client                      - Do not install the python BinSync client.
  --version                        - Display the version of this program
  --help                           - Display available options

EXAMPLES:
  install.sh --ida /home/mahaloz/idapro-7.6/plugins/
  install.sh --binja \"/Applications/Binary Ninja.app/Contents/MacOS/plugins/\"
  install.sh --angr /home/mahaloz/angr-dev/angr-management/angrmanagement/plugins/ --link
  install.sh --ida /home/mahaloz/idapro-7.6/plugins/ --no-client
  install.sh --help


Please only use this script inside the BinSync repo directory.
"
exit 1
}

LOC="$(realpath .)"
validate_in_binsync() {
    if test -f "${LOC}/binsync/client.py"; then
        :
    else
        echo "ERROR: not in BinSync repo directory!"
        print_help
    fi
}

version() {
    VERSION=$(grep -o "[0-9].[0-9].[0-9]" setup.py)
    echo "VERSION: ${VERSION}"
    exit 0
}

install_ida() {
    if [ -z "${LINK}" ]; then
        echo "INSTALLING: IDA plugin without linking to ${IDA_PATH}"
        cp -r "${LOC}/plugins/ida_binsync/*" "$IDA_PATH" && \
        echo "IDA install was successful!" && \
        return
    else
        echo "INSTALLING: IDA plugin WITH linking to ${IDA_PATH}"
        ln -s "${LOC}/plugins/ida_binsync/ida_binsync.py" "$IDA_PATH" && \
        ln -s "${LOC}/plugins/ida_binsync/ida_binsync/" "$IDA_PATH" && \
        echo "IDA install was successful!" && \
        return
    fi
    echo -e "ERROR: IDA install failed!\n"
}

install_binja() {
    if [ -z "${LINK}" ]; then
        echo "INSTALLING: Binja plugin without linking to ${BINJA_PATH}"
        cp -r "${LOC}/plugins/binja_binsync/" "$BINJA_PATH" && \
        echo "Binja install was successful!" && \
        return
    else
        echo "INSTALLING: Binja plugin WITH linking to ${BINJA_PATH}"
        ln -s "${LOC}/plugins/binja_binsync/" "$BINJA_PATH" && \
        echo "Binja install was successful!" && \
        return
    fi
    echo -e "ERROR: Binja install failed!\n"
}

install_angr() {
    if [ -z "${LINK}" ]; then
        echo "INSTALLING: angr plugin without linking to ${ANGR_PATH}"
        cp -r "${LOC}/plugins/angr_binsync/" "$ANGR_PATH" && \
        echo "angr install was successful!" && \
        return
    else
        echo "INSTALLING: angr plugin WITH linking to ${ANGR_PATH}"
        ln -s "${LOC}/plugins/angr_binsync/" "$ANGR_PATH" && \
        echo "angr install was successful!" && \
        return
    fi
    echo -e "ERROR: angr install failed!\n"
}

install_client() {
    pip install -e .
    echo "BinSync Core installed successfully!"
}

POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    -i|--ida)
      IDA_PATH="$2"
      shift # past argument
      shift # past value
      ;;
    -b|--binja)
      BINJA_PATH="$2"
      shift # past argument
      shift # past value
      ;;
    -a|--angr)
      ANGR_PATH="$2"
      shift # past argument
      shift # past value
      ;;
    --link)
      LINK=YES
      shift # past argument
      ;;
    --no-client)
      NO_CLIENT=YES
      shift # past argument
      ;;
    -v|--version)
      SHOW_VERSION=YES
      shift # past argument
      ;;
    -h|--help)
      SHOW_HELP=YES
      shift # past argument
      ;;
    -*|--*)
      echo "Unknown option $1"
      print_help
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1") # save positional arg
      shift # past argument
      ;;
  esac
done

set -- "${POSITIONAL_ARGS[@]}" # restore positional parameters

if [[ -n $1 ]]; then
    print_help
else
    if [ -n "${SHOW_HELP}" ]; then
        print_help
    elif [ -n "${SHOW_VERSION}" ]; then
        version
        exit 0
    fi

    # plugins
    if [ -n "${IDA_PATH}" ]; then
        install_ida
    fi
    if [ -n "${BINJA_PATH}" ]; then
        install_binja
    fi
    if [ -n "${ANGR_PATH}" ]; then
        install_angr
    fi

    # client
    if [ -z ${NO_CLIENT} ]; then
        install_client
    fi
fi