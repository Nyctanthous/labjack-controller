import labjack.ljm as ljm
from labjack.ljm import constants as ljm_constants, \
                        errorcodes as ljm_errorcodes
from labjack.ljm.ljm import LJMError

import numpy as np
import pandas as pd
from typing import List, Tuple, Union
from math import ceil
import sys
import time
import datetime
import ctypes
import warnings
from ctypes import c_int32
from colorama import init, Fore
init()


def calculate_max_speed(device: str, num_channels: int, gain: int,
                        resolution: int) -> float:
    # Values derived from
    # https://labjack.com/support/datasheets/t-series/appendix-a-1
    if device == "T7":
        if num_channels == 1:
            return (100000 if resolution == 1 else
                    48000 if resolution == 2 else
                    22000 if resolution == 3 else
                    11000 if resolution == 4 else
                    5500 if resolution == 5 else
                    2500 if resolution == 6 else
                    1200 if resolution == 7 else
                    600 if resolution == 8 else
                    -1)
        else:
            return {
                (1, 1): 100000,
                (10, 1): 8200,
                (100, 1): 1700,
                (1000, 1): -1,
                (1, 2): 39600,
                (10, 2): 7200,
                (100, 2): 800,
                (1000, 2): -1,
                (1, 3): 19800,
                (10, 3): 2800,
                (100, 3): -1,
                (1000, 3): -1,
                (1, 4): 9800,
                (10, 4): 2600,
                (100, 4): -1,
                (1000, 4): -1,
                (1, 5): 4400,
                (10, 5): 1300,
                (100, 5): -1,
                (1000, 5): -1,
                (1, 6): 2600,
                (10, 6): 640,
                (100, 6): -1,
                (1000, 6): -1,
                (1, 7): 1300,
                (10, 7): 440,
                (100, 7): -1,
                (1000, 7): -1,
                (1, 8): 630,
                (10, 8): 400,
                (100, 8): -1,
                (1000, 8): -1,
            }.get((gain, resolution), -1) / num_channels
    elif device == "T4":
        return {
            1: 50000,
            2: 15000,
            3: 8000,
            4: 4000,
            5: 2000
        }.get(resolution, -1) / num_channels
    else:
        return -1


def time_ns_func():
    return time.time_ns() / 1e9


time_func = time.time if sys.version_info < (3, 7, 0) else time_ns_func


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls) \
                .__call__(*args, **kwargs)
        return cls._instances[cls]


class LJMLibrary(metaclass=Singleton):
    # Base reference to the staticlib.
    staticlib = None
    ljm_buffer = {}
    ljm_is_open = {}

    def __init__(self):
        os_is = sys.platform.startswith
        try:
            self.staticlib = (ctypes.WinDLL("LabJackM.dll") if os_is("win32") else
                              ctypes.CDLL("LabJackM.dll") if os_is("cygwin") else
                              ctypes.CDLL("libLabJackM.so") if os_is("linux") else
                              ctypes.CDLL("libLabJackM.dylib") if os_is("darwin")
                              else None)
        except Exception as e:
            if os_is("darwin"):
                try:
                    self.staticlib = ctypes.CDLL("/usr/local/lib/libLabJackM.dylib")
                except Exception:
                    pass

            raise LJMError(errorString="Cannot load the LJM library %s.\n%s"
                           % (str(self.staticlib), str(e)))

        if not self.staticlib:
            raise LJMError(errorString="Cannot load the LJM library."
                           " Unsupported platform %s." % sys.platform)

    def _validate_handle(self, handle: int, stream_mode=False) -> None:
        """
        Internal method to validate the handle that a user provides,
        ensuring we have connected to it.

        Parameters
        ----------
        handle : int
            A valid handle to a LJM device that has an opened connection.
        stream_mode : bool, optional
            Perform validation checking for stream_mode, additionally ensuring
            that we have already started the stream.

        Returns
        -------
        None

        Raises
        ------
        Exception
            If the handle specified does not have a connection to close.
        KeyError, optional
            Only is checked if stream_mode = True. Happens when the handle
            specified does not have a buffer associated with it, meaning the
            stream initialization has not happened or was originally not
            successful.
        """
        if stream_mode and handle not in self.ljm_buffer:
            raise KeyError("Cannot find handle %s in the collection of known"
                           " connections.")

        if not self.ljm_is_open[handle]:
            raise Exception("The connection for this device is not open.")

    def _num_to_ipv4(self, num: int) -> str:
        """
        Internal method used to convert the LJM library's integer
        representation of IP addresses to a string representation of the same.
        """
        ip_str = ("\0" * ljm_constants.IPv4_STRING_SIZE).encode("ascii")

        error = self.staticlib.LJM_NumberToIP(ctypes.c_uint32(num), ip_str)
        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        return str(ip_str.decode("ascii", "ignore").split("\0", 1)[0])

    def _names_to_modbus_addresses(self, names: List[str]):
        num_frames = len(names)

        names = [name.encode("ascii") for name in names
                 if isinstance(name, str)]

        if num_frames != len(names):
            raise TypeError("Expected a list of strings.")

        names = (ctypes.c_char_p * len(names))(*names)

        # Array that the LJM library populates with the addresses of the named
        # channels.
        address_arr = (c_int32 * num_frames)()

        # Array the LJM needs to populate listing the types of the registers.
        # Not very useful in Python.
        type_arr = (c_int32 * num_frames)()

        ref = ctypes.byref
        error = self.staticlib.LJM_NamesToAddresses(c_int32(num_frames),
                                                    ref(names),
                                                    ref(address_arr),
                                                    ref(type_arr))
        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        return address_arr

    def connection_close(self, handle: str):
        """
        Based on the LJM function LJM_Close. Closes the connection associated
        with the device handle, freeing it for usage elsewhere.

        Parameters
        ----------
        handle: str
            A valid handle to a LJM device that has an opened
            connection.

        Returns
        -------
        None

        Raises
        ------
        Exception
            If the handle specified does not have a connection to close.
        LJMError
            If the LJM library cannot close the valid handle.
        """
        self._validate_handle(handle)

        error = self.staticlib.LJM_Close(handle)
        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        self.ljm_is_open[handle] = False

    def connection_close_all(self):
        """
        Based on the LJM function LJM_CloseAll. Closes all connections opened
        with all devices, freeing them for usage elsewhere.

        Parameters
        ----------
        None

        Returns
        -------
        None

        Raises
        ------
        LJMError
            If the LJM library cannot close a handle.

        """

        error = self.staticlib.LJM_CloseAll()
        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        # Empty the dict.
        self.ljm_is_open.clear()

    def connection_info(self, handle: int):
        """
        Based on the LJM function LJM_GetHandleInfo. Returns attribute info
        about the device associated with the selected handle.

        Parameters
        ----------
        handle: int
            A valid handle to a LJM device that has an opened connection.

        Returns
        -------
        device_type: str
            A device type in ("T7", "T4")
        connection_type: str
            String representation of the connection method, is a string in
            ("USB", "WIFI", "ETHERNET")
        serial_num: int
            The serial number of the device
        ipv4_address: str
            String representation of the device's IP address, when the
            connection method indicates this property is applicable.
            Is otherwise None.
        port: int
            The port of the device that the connection is accessed at,
            assuming that ipv4_address is defined.
        max_packet_size: int
            The maximum packet size that can be sent or recieved from the
            device. Varies depending on the device and connection type.

        Raises
        ------
        Exception
            If the handle specified does not have a connection to close.
        LJMError
            If the LJM library cannot get information about the handle.
        """
        self._validate_handle(handle)

        device_type = ctypes.c_int32(0)
        connection_type = ctypes.c_int32(0)
        serial_num = ctypes.c_int32(0)
        ipv4_address = ctypes.c_int32(0)
        port = ctypes.c_int32(0)
        max_packet_size = ctypes.c_int32(0)

        error = ljm_reference.staticlib\
            .LJM_GetHandleInfo(handle,
                               ctypes.byref(device_type),
                               ctypes.byref(connection_type),
                               ctypes.byref(serial_num),
                               ctypes.byref(ipv4_address),
                               ctypes.byref(port),
                               ctypes.byref(max_packet_size))
        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        device_name = ("T7" if device_type.value == ljm_constants.dtT7 else
                       "T4" if device_type.value == ljm_constants.dtT4 else
                       "Other")
        connection_type = connection_type.value
        connection_name = ("USB" if connection_type == ljm_constants.ctUSB else
                           "WIFI" if connection_type == ljm_constants.ctWIFI else
                           "Ethernet" if connection_type == ljm_constants.ctETHERNET
                           else "Other")
        return device_name, connection_name, serial_num.value, \
            self._num_to_ipv4(ipv4_address.value), port.value, \
            max_packet_size.value

    def connection_open(self, device_type: str, connection_type: str,
                        device_id) -> int:
        """
        Opens a connection to the device with the designated inputs.

        Parameters
        ----------
        device_type : str
            A LabJack model. Valid options are
            'ANY' for any arbitrary device connected.
            'T7' for the T7 and T7 Pro
            'T4' for the T4
            'DIGIT' for Digit series devices.
        connection_type : str
            Valid options are
            'ANY' for attempting any mode of connection
            'USB' for attempting connection over USB
            'ETHERNET' for attempting connection over Ethernet
            'WIFI' for attempting connection over WiFi
            Support for each of these modes is a function of the LabJack model
            you are connecting to. When 'ANY' is selected, the LabJack library
            generally defaults to a USB connection.
        device_identifier : str
            The user-designated name of the LabJack device, or an IP address
            if approperiate.

        Returns
        -------
        handle : int
            A handle that is used as a reference to the connection to the
            Labjack device.

        Raises
        ------
        LJMError
            If the LJM library cannot open a connection.
        TypeError
            If the type of an input is invalid.
        ValueError
            If a value provided as an argument is invalid.
        """
        if not isinstance(device_type, str):
            raise TypeError("device_type error: expected a string instead"
                            " of %s."
                            % str(type(device_type)))
        if device_type not in ["T7", "T4", "DIGIT", "ANY"]:
            raise ValueError("Expected device type to be either \"T7\","
                             " \"T4\", \"DIGIT\", or \"ANY\"")
        if not isinstance(connection_type, str):
            raise TypeError("connection_type error: expected a string instead"
                            " of %s."
                            % str(type(connection_type)))
        if connection_type not in ["USB", "ETHERNET", "WIFI", "ANY"]:
            raise ValueError("Expected connection type to be either"
                             "\"USB\", \"ETHERNET\", \"WIFI\", or \"ANY\"")
        if not (isinstance(device_id, str) or isinstance(device_id, int)):
            raise TypeError("device_identifier error: expected a string"
                            " or an int instead of %s."
                            % str(type(device_id)))
        temp_handle = ctypes.c_int32(0)

        if isinstance(device_id, int):
            device_id = str(device_id)

        error = self.staticlib \
            .LJM_OpenS(device_type.encode("ascii"),
                       connection_type.encode("ascii"),
                       device_id.encode("ascii"),
                       ctypes.byref(temp_handle))

        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        # Note that the connection is now open.
        self.ljm_is_open[temp_handle.value] = True

        return temp_handle.value

    def list_all(self) -> Tuple[str, str, str, str]:
        num_found = ctypes.c_int32(0)
        dev_types = (ctypes.c_int32 * ljm_constants.LIST_ALL_SIZE)()
        conn_types = (ctypes.c_int32 * ljm_constants.LIST_ALL_SIZE)()
        ser_nums = (ctypes.c_int32 * ljm_constants.LIST_ALL_SIZE)()
        ip_addrs = (ctypes.c_int32 * ljm_constants.LIST_ALL_SIZE)()

        error = self.staticlib.LJM_ListAll(ljm_constants.dtANY,
                                           ljm_constants.ctANY,
                                           ctypes.byref(num_found),
                                           ctypes.byref(dev_types),
                                           ctypes.byref(conn_types),
                                           ctypes.byref(ser_nums),
                                           ctypes.byref(ip_addrs))

        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        num_found = num_found.value

        dev_types = dev_types[0:num_found][:]
        conn_types = conn_types[0:num_found][:]
        ser_nums = ser_nums[0:num_found][:]
        ip_addrs = ip_addrs[0:num_found][:]

        for i in range(0, num_found):
            dev_types[i] = ("T7" if dev_types[i] == ljm_constants.dtT7 else
                            "T4" if dev_types[i] == ljm_constants.dtT4 else
                            "Other")
            conn_types[i] = ("USB" if conn_types[i] == ljm_constants.ctUSB else
                             "WIFI" if conn_types[i] == ljm_constants.ctWIFI else
                             "Ethernet" if conn_types[i] == ljm_constants.ctETHERNET
                             else "Other")
            ip_addrs[i] = self._num_to_ipv4(ip_addrs[i])

        return list(zip(*[dev_types, conn_types, ser_nums, ip_addrs]))

    def stream_read(self, handle: int) -> Tuple[ctypes.c_double, int, int]:
        """
        Returns data from a LabJack device with an open connection that is
        currently streaming.

        Parameters
        ----------
        handle: int
            A valid handle to a LJM device that has an opened connection.

        Returns
        -------
        packet_data : c_double
            A c_double array with stream data. All channels are ordered
            sequentially.
        device_buffer_backlog : int
            The number of scans left in the device buffer, as measured from
            when data was last collected from the device. This should usually
            be near zero and not growing.
        ljm_buffer_backlog : int
            The number of scans left in the LJM buffer, as measured from after
            the data returned from this function is removed from the LJM
            buffer. This should usually be near zero and not growing.

        Raises
        ------
        KeyError
            If the handle specified does not have a buffer associated with it,
            meaning the stream initialization has not happened or was
            originally not successful.
        Exception
            If the handle specified does not have a connection to close.
        LJMError
            If the LJM library cannot read from the device.
        """

        self._validate_handle(handle, stream_mode=True)

        # Initialize variables that we'll populate with results
        packet_data = (ctypes.c_double * self.ljm_buffer[handle])()
        dev_buffer_backlog = ctypes.c_int32(0)
        ljm_buffer_backlog = ctypes.c_int32(0)

        # Actually read data from the device
        error = self.staticlib.LJM_eStreamRead(handle,
                                               ctypes.byref(packet_data),
                                               ctypes.byref(dev_buffer_backlog),
                                               ctypes.byref(ljm_buffer_backlog))
        # Handle errors if they occured
        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        return packet_data, dev_buffer_backlog.value, \
            ljm_buffer_backlog.value

    def stream_start(self, handle: int, scan_list: List[str], scan_rate: float,
                     scans_per_read: int) -> float:
        """
        Based on the LJM function LJM_eStreamStart. Creates a buffer that the
        LJM device can record data, and then makes the device start streaming
        into this buffer.

        Parameters
        ----------
        handle : int
            A valid string handle to a LJM device that has an opened
            connection.
        scan_list : List[str]
            List of addresses ("AIN0", etc) to scan from.
        scan_rate : float
            The rate in Hz that all specified addresses will be read at.
        scans_per_read : int
            When reading data from the stream, this value determines how
            many scans are returned. There is no relation between this
            parameter and the device's maximum packet size.

        Returns
        -------
        actual_scan_rate: float
            The actual frequency the device will scan at.

        Raises
        ------
        Exception
            If the handle specified does not have a connection to stream on.
        LJMError
            If the LJM library cannot get information about the handle.
        """

        self._validate_handle(handle)

        scan_rate = ctypes.c_double(scan_rate)
        num_addrs = len(scan_list)
        scan_list = self._names_to_modbus_addresses(scan_list)
        self.ljm_buffer[handle] = scans_per_read * num_addrs

        error = self.staticlib.LJM_eStreamStart(handle,
                                                c_int32(scans_per_read),
                                                c_int32(num_addrs),
                                                ctypes.byref(scan_list),
                                                ctypes.byref(scan_rate))

        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

        return scan_rate.value

    def stream_stop(self, handle: int) -> None:
        """
        Based on the LJM function LJM_eStreamStop. Frees the buffer that the
        LJM device records data into, and then makes the device stop streaming.

        Parameters
        ----------
        handle: int
            A valid handle to a LJM device that has an opened connection.

        Returns
        -------
        None

        Raises
        ------
        KeyError
            If the handle specified does not have a buffer associated with it,
            meaning the stream initialization has not happened or was
            originally not successful.
        Exception
            If the handle specified does not have a connection to close.
        LJMError
            If the LJM library cannot get information about the handle.
        """

        self._validate_handle(handle, stream_mode=True)

        del self.ljm_buffer[handle]

        error = self.staticlib.LJM_eStreamStop(handle)
        if error != ljm_errorcodes.NOERROR:
            raise LJMError(error)

    def modify_settings(self, **kwargs):
        """
        Based on the LJM function writeLibraryConfigS. Writes a configuration
        value to the Labjack library itself.

        Parameters
        ----------
        **kwargs
            A LJM library setting. Is from the following:
            ensure_updated: bool
                Whether or not the LJM will check if the current Labjack
                device is using the latest firmware.
            multiple_feedbacks: bool
                Whether or not the LJM will send multiple packets when the
                desired operation would exceed the maximum size of one packet.
            retry_on_transaction_err: bool
                Whether or not LJM automatically retries an operation if a
                LJME_TRANSACTION_ID_ERR occurs.
            stream_timeout: float
                How long in MS the LJM waits for a packet to be sent or
                received.

        Returns
        -------
        None

        """
        setting = ""
        value = -1

        for kwarg in kwargs:
            # Handle simple boolean settings first.
            setting = (b'LJM_ALLOWS_AUTO_MULTIPLE_FEEDBACKS' if kwarg is "multiple_feedbacks" else
                       b'LJM_OLD_FIRMWARE_CHECK' if kwarg is "ensure_updated" else
                       b'LJM_RETRY_ON_TRANSACTION_ID_MISMATCH' if kwarg is "retry_on_transaction_err" else
                       "")
#                    "LJM_SEND_RECEIVE_TIMEOUT_MS" \
#                    if kwarg is "communication_timeout" else ""

            if setting:
                value = ctypes.c_double(1) if kwargs[kwarg] \
                    else ctypes.c_double(0)
                try:
                    error = self.staticlib.LJM_WriteLibraryConfigS(setting,
                                                                   value)
                except Exception as e:
                    print("Unexpected error writing to LJM library" + e)
                if error != ljm_errorcodes.NOERROR:
                    raise LJMError(error)
                continue


ljm_reference = LJMLibrary()


class LabjackReader(object):
    """A class designed to represent an arbitrary LabJack device."""

    # Keep track of the input channels we're reading.
    input_channels = []

    # Declare a data storage handle, is a ctypes.c_int with some number
    # (in other words, is a C array)
    data_arr = None

    # Also, specify the largest index that is populated.
    _max_index = 0

    # There will be an int handle for the LabJack device
    handle = -1

    connection_open = False

    # For administrative purposes, we will also keep track of the
    # self-reported metadata of this device.
    meta_device = None
    meta_connection = None
    meta_serial_number = 0
    meta_ip_addr = None
    meta_port = 0
    meta_max_packet_size = 0

    def __init__(self,
                 device_type: str,
                 connection_type="ANY",
                 device_identifier="ANY") -> None:
        """
        Initialize a LabJack object.

        Parameters
        ----------
        device_type : str
            A LabJack model. Valid options are
            'ANY' for any arbitrary device connected.
            'T7' for the T7 and T7 Pro
            'T4' for the T4
            'DIGIT' for Digit series devices.
        connection_type : str, optional
            Valid options are
            'ANY' for attempting any mode of connection
            'USB' for attempting connection over USB
            'ETHERNET' for attempting connection over Ethernet
            'WIFI' for attempting connection over WiFi
            Support for each of these modes is a function of the LabJack model
            you are connecting to. When 'ANY' is selected, the LabJack library
            generally defaults to a USB connection.
        device_identifier : str, optional
            The user-designated name of the LabJack device, or an IP address
            if approperiate.

        Returns
        -------
        LabjackReader
            A new instance of a LabjackReader.

        Raises
        ------
        LJMError
            If the LJM library cannot open a connection.
        TypeError
            If the type of an input is invalid.
        ValueError
            If a value provided as an argument is invalid.
        """
        if not isinstance(device_type, str):
            raise TypeError("device_type error: expected a string instead"
                            " of %s."
                            % str(type(device_type)))
        if device_type not in ["T7", "T4", "DIGIT", "ANY"]:
            raise ValueError("Expected device type to be either \"T7\","
                             " \"T4\", \"DIGIT\", or \"ANY\"")
        if not isinstance(connection_type, str):
            raise TypeError("connection_type error: expected a string instead"
                            " of %s."
                            % str(type(connection_type)))
        if connection_type not in ["USB", "ETHERNET", "WIFI", "ANY"]:
            raise ValueError("Expected connection type to be either"
                             "\"USB\", \"ETHERNET\", \"WIFI\", or \"ANY\"")
        if not (isinstance(device_identifier, str)
                or isinstance(device_identifier, int)):
            raise TypeError("device_identifier error: expected a string"
                            " or an int instead of %s."
                            % str(type(device_identifier)))
        self.device_type, self.connection_type = device_type, connection_type
        self.device_identifier = device_identifier

    def __enter__(self):
        self.open(verbose=False)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __str__(self):
        return self.__repr__() + " Max packet size in bytes: %i" \
               % (self.meta_max_packet_size)

    def __repr__(self):
        # Make sure we have a connection open.
        self.open(verbose=False)

        # If we don't have enough metadata abut this device, get it.
        if not (self.meta_device and self.meta_connection
                and self.meta_serial_number
                and self.meta_ip_addr
                and self.meta_port
                and self.meta_max_packet_size):

            self.meta_device, self.meta_connection, \
                self.meta_serial_number, self.meta_ip_addr, \
                self.meta_port, self.meta_max_packet_size = \
                ljm_reference.connection_info(self.handle)

        return "LabjackReader('Type': %s, Connection': %s, 'Serial': %i," \
            " 'IP': %s, 'Port': %i)" \
            % (self.meta_device, self.meta_connection, self.meta_serial_number,
               self.meta_ip_addr, self.meta_port)

    @property
    def connection_status(self):
        """
        Get the status of the connection to the LabJack

        Parameters
        ----------
        None

        Returns
        -------
        connection_open: bool
            True if the connection is open
            False if the connection is closed/does not exist

        """
        return self.connection_open

    @property
    def max_row(self) -> int:
        """
        Return the number of the last row that currently exists.

        Parameters
        ----------
        None

        Returns
        -------
        row: int
            The number of the last row recorded in the data array,
            or -1 on error
        """
        if self.max_index < 1:
            return -1
        # Else...
        return int(self.max_index / (len(self.input_channels) + 1))

    @property
    def max_index(self) -> int:
        """
        Return the largest index value that has been filled.

        Parameters
        ----------
        None

        Returns
        -------
        max_index: int
            The index of the latest value that has been recorded.

        """
        if self._max_index is not None and self._max_index:
            return self._max_index
        else:
            return -1

    @max_index.setter
    def max_index(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("Invalid type provided.")
        if value < -1:
            raise ValueError("Invalid value provided, must be greater than or"
                             " equal to -1.")

        self._max_index = value

    def _reshape_data(self, from_row: int, to_row: int):
        """
        Get a range of rows from the recorded data

        Parameters
        ----------
        from_row: int
            The first row to include, inclusive.
        to_row: int
            The last row to include, non-inclusive.

        Returns
        -------
        array_like: ndarray
            A 2D array, starting at from_row, of data points, where
            every row is one data point across all channels.
        """
        if (self.data_arr is not None and self.max_index != -1
           and from_row >= 0):
            row_width = len(self.input_channels) + 2
            max_index = min(self.max_index, row_width * to_row)

            start_index = from_row * row_width

            return np.array(self.data_arr[start_index:max_index]) \
                .reshape((ceil((max_index - start_index) / row_width),
                         row_width))
        # Else...
        return None

    def _close_stream(self, verbose=False) -> None:
        """
        Close a streaming connection to the LabJack.

        Parameters
        ----------
        None

        Returns
        -------
        None

        """
        try:
            # Try to close the stream
            ljm_reference.stream_stop(self.handle)
            self.connection_open = False
            if verbose:
                print("\nStream stopped.")
        except Exception:
            # No stream running, probably.
            if verbose:
                print("Could not stop stream, possibly because there is no"
                      " stream running.")
            pass

    def _setup(self, inputs, inputs_max_voltages, stream_setting, resolution,
               scan_rate, scans_per_read=-1) -> Tuple[int, int]:
        """
        Set up a connection to the LabJack for streaming

        Parameters
        ----------
        inputs: sequence of strings
            Names of input channels on the LabJack device to read.
            Must correspond to the actual name on the device.
        inputs_max_voltages: sequence of real values
            Maximum voltages corresponding element-wise to the channels
            listed in inputs.
        stream_setting: int, optional
            See official LabJack documentation.
        resolution: int, optional
            See official LabJack documentation.
        scan_rate: int
            Number of times per second (Hz) the device will get a datapoint for
            each of the channels specified.
        scans_per_read: int, optional
            Number of data points contained in a packet sent by the LabJack
            device. -1 indicates the maximum possible sample rate.

        Returns
        -------
        scan_rate : int
            The actual scan rate the device starts at
        scans_per_read : int
            The actual sample rate the device starts at

        """
        # Sanity check on inputs
        max_scan_rate = int(calculate_max_speed(self.device_type, len(inputs),
                                                int(max(inputs_max_voltages)),
                                                resolution))
        if max_scan_rate < 0:
            warnings.warn("Maximum valid scan rate is not known for this"
                          " configuration or device. Proceed at your own"
                          " risk.", RuntimeWarning)
        max_scan_rate = int(max_scan_rate / 2)

        # Verify scan_rate first.
        if scan_rate > max_scan_rate:
            warnings.warn("Maximum valid scan rate is less than the value"
                          " provided. Setting to highest valid value.",
                          UserWarning)
            scan_rate = max_scan_rate

        # Next, verify the scans/read.
        if scans_per_read == -1:
            scans_per_read = scan_rate
        elif scans_per_read > scan_rate:
            warnings.warn("Maximum valid scan/read rate is larger than"
                          " the scan rate. Setting to be equal to the scan"
                          " rate.", UserWarning)
            scans_per_read = scan_rate

        # If a packet is lost, don't try and get it again.
        ljm_reference.modify_settings(retry_on_transaction_err=False)

        # When streaming, negative channels and ranges can be configured
        # for individual analog inputs, but the stream has only one
        # settling time and resolution.

        if self.device_type == "T7":
            # Ensure triggered stream is disabled.
            self.modify_settings(triggered_stream=None)

            # Enabling internally-clocked stream.
            self.modify_settings(stream_clock="internal")

        # All negative channels are single-ended, AIN0 and AIN1 ranges are
        # +/-10 V, stream settling is 0 (default) and stream resolution
        # index is 0 (default).
        names = ("AIN_ALL_NEGATIVE_CH",
                 *[element + "_RANGE" for element in inputs],
                 "STREAM_SETTLING_US", "STREAM_RESOLUTION_INDEX")
        values = (ljm_constants.GND, *inputs_max_voltages,
                  stream_setting, resolution)

        # Write the analog inputs' negative channels (when applicable),
        # ranges, stream settling time and stream resolution configuration.
        num_frames = len(names)
        ljm.eWriteNames(self.handle, num_frames, names, values)

        # Configure and start stream
        return ljm_reference.stream_start(self.handle, inputs, scan_rate,
                                          scans_per_read), scans_per_read

    def open(self, verbose=True) -> None:
        """
        Open a connection to the LabJack to allow for streaming or other
        device communication.

        Parameters
        ----------
        verbose : bool, optional
            Sends information about the attempt to open the device to standard
            out.

        Returns
        -------
        None

        """
        if not self.connection_open:
            # Open our device.
            self.handle = ljm_reference.connection_open(self.device_type,
                                                        self.connection_type,
                                                        self.device_identifier)
            self.connection_open = True

            if verbose:
                print(self)

    def close(self):
        """
        Close a connection to the LabJack, allowing others to connect to this
        object's labjack via the connections used by this object.

        Parameters
        ----------
        None

        Returns
        -------
        None

        """
        self._close_stream()
        ljm_reference.connection_close(self.handle)
        self.connection_open = False

    def modify_settings(self, **kwargs):
        """
        Based on the LJM function eWriteName. Writes a configuration value to
        our Labjack device.

        Parameters
        ----------
        **kwargs
            A device setting. Is from the following:
            ain_on: bool
                Set to True if you want the AIN analog inputs to be avalible,
                else set to False. Setting is T7-specific, will be ignored on
                other devices.
            ain_on_default: bool
                Set to True if you want the AIN analog inputs to be avalible
                by default, else set to False. Setting is T7-specific, will
                be ignored on other devices.
            ethernet_on: bool
                Set to True if you want the device to have Ethernet avalible,
                else set to False.
            ethernet_ip_default: str
                A string representing the IP address this device should use
                by default when connecting by Ethernet.
            ethernet_subnet_default: str
                A string representing the subnet this device should use
                by default when connecting by Ethernet.
            ethernet_on_default: bool
                Set to True if you want the device to have Ethernet avalible
                by default, else set to False.
            led_on: bool
                Set to True if you want the LED to be on/blinking,
                else set to False.
            led_on_default: bool
                Set to True if you want the LED to be on/blinking by default,
                else set to False.
            stream_clock: str
                T7 Only. Controls which clock source will be used to run the
                main stream clock. Rising edges will increment a counter and
                trigger a stream scan after the number of edges specified
                in the setting stream_clock_divisor. Is one of the following:
                "internal": Use the internal crystal to clock the stream.
                "external": Use an external clock plugged into CIO3.
            stream_clock_divisor: int
                Not Implemented
            triggered_stream: str
                T7 Only. Set to None if you don't want to start the stream
                when an input is given to one of the FIO0 or FIO1 inputs.
                Else, set to one of the following channels to listen on:
                DIO_EF0
                DIO_EF1
                DIO_EF2
                DIO_EF3
                DIO_EF6
                DIO_EF7
            wifi_on: bool
                Set to True if you want the device to have WIFI avalible,
                else set to False.
            wifi_on_default: bool
                Set to True if you want the device to have WIFI avalible by
                default, else set to False.
            wifi_ip_default: str
                Not Implemented


        Returns
        -------
        None
        """

        # NOTE: WIFI SETTINGS NEED TO BE SET AND THEN APPLIED WITH
        # WIFI_APPLY_SETTINGS!

        setting = ""
        value = -1

        for kwarg in kwargs:
            # Handle boolean settings first.
            setting = (b'POWER_AIN' if kwarg is "ain_on" else
                        b'POWER_AIN_DEFAULT' if kwarg is "ain_on_default" else
                        b'POWER_ETHERNET' if kwarg is "ethernet_on" else
                        b'POWER_ETHERNET_DEFAULT' if kwarg is "ethernet_on_default" else
                        b'POWER_LED' if kwarg is "led_on" else
                        b'POWER_LED_DEFAULT' if kwarg is "led_on_default" else
                        b'POWER_WIFI' if kwarg is "wifi_on" else
                        b'POWER_WIFI_DEFAULT' if kwarg is "wifi_on_default" else
                        "")

            if setting:
                value = ctypes.c_double(1) if kwargs[setting] \
                    else ctypes.c_double(0)
                error = ljm_reference.staticlib \
                    .LJM_eWriteName(self.handle, setting, value)
                if error != ljm_errorcodes.NOERROR:
                    raise LJMError(error)
                continue

            # Now, handle more complex operations.
            if kwarg is "triggered_stream":
                if kwargs[kwarg] is None:
                    error = ljm_reference.staticlib \
                        .LJM_eWriteName(self.handle,
                                        b'STREAM_TRIGGER_INDEX',
                                        ctypes.c_double(0))
                elif isinstance(kwargs[kwarg], str):
                    value = (2000 if kwargs[kwarg] is "DIO_EF0" else
                             2001 if kwargs[kwarg] is "DIO_EF1" else
                             2002 if kwargs[kwarg] is "DIO_EF2" else
                             2003 if kwargs[kwarg] is "DIO_EF3" else
                             2004 if kwargs[kwarg] is "DIO_EF4" else
                             2005 if kwargs[kwarg] is "DIO_EF5" else
                             2006 if kwargs[kwarg] is "DIO_EF6" else
                             2007 if kwargs[kwarg] is "DIO_EF7" else
                             0)
                    if value:
                        # Write the corresponding value.
                        error = ljm_reference.staticlib \
                            .LJM_eWriteName(self.handle,
                                            b'STREAM_TRIGGER_INDEX',
                                            ctypes.c_double(value))
                        # TODO: LJM_STREAM_SCANS_RETURN_ALL? See
                        # https://labjack.com/support/software/api/ljm/function-reference/ljmestreamstart#triggered
                    else:
                        raise ValueError("Expected an argument in the range"
                                         "DIO_EF0....DIO_EF7")
                else:
                    raise TypeError("Invalid argument. Expected a string"
                                    "in the range DIO_EF0....DIO_EF7")
            elif kwarg is "stream_clock":
                value = (0 if kwargs[kwarg] is "internal" else
                         2 if kwargs[kwarg] is "external" else
                         -1)
                if value == -1:
                    raise ValueError("Expected an argument that was either"
                                     "\"internal\" or \"external\"")
                else:
                    error = ljm_reference.staticlib \
                            .LJM_eWriteName(self.handle,
                                            b'STREAM_CLOCK_SOURCE',
                                            ctypes.c_double(value))

            # Finally, ensure we didn't get any errors trying to set our
            # configuration.
            if error != ljm_errorcodes.NOERROR:
                raise LJMError(error)

    def find_max_freq(self,
                      inputs: List[str],
                      inputs_max_voltages: List[float],
                      stream_setting=0,
                      resolution=0,
                      verbose=True,
                      max_buffer_size=0,
                      num_seconds=45):
        """
        Determine the maximum frequency and number of elements per packet this
        device can sample at without overflowing any buffers.

        Parameters
        ----------
        inputs : sequence of strings
            Names of input channels on the LabJack device to read.
            Must correspond to the actual name on the device.
        inputs_max_voltages : sequence of real values
            Maximum voltages corresponding element-wise to the channels
            listed in inputs.
        stream_setting : int, optional
            See official LabJack documentation.
        resolution : int, optional
            See official LabJack documentation.
        verbose : str, optional
            If enabled, will print out statistics about each read.
        num_seconds : int, optional
            The amount of time to test a given configuration.

        Returns
        -------
        scan_rate : int
            Number of times per second (Hz) the device will get a data point
            for each of the channels specified.
        scans_per_read : int, optional
            Number of data points contained in a packet sent by the LabJack
            device.

        Examples
        --------
        Find out the maximum (Hz, scans/packet) an arbitrary LabJack T7 can
        read from the channels AIN0, AIN1 each having a maximum voltage of
        10V.

        Output varies depending upon many factors, such as connection method
        to the LabJack, the device itself, and so forth.

        >>> reader = LabjackReader("T7")
        >>> reader.find_max_freq(["AIN0", "AIN1"], [10.0, 10.0])
        (57400.0, 1024)

        """

        # Close any existing streams.
        self._close_stream()

        if verbose:
            print("%s %15s %16s %15s %15s %15s"
                  % ("Success", "Scan Rate (Hz)", "Search Range (Hz)",
                     "Scans on Device", "Scans on LJM", "Skips"))

        MAX_BUFFERSIZE = 0
        MAX_LJM_BUFFERSIZE = 0
        min_rate = 0
        med_rate = 100
        max_rate = 0

        exponential_mode = True

        # The number of elements we get back in a packet.
        scans_per_read = 1

        last_good_rate = -1
        last_good_scan_per_packet = -1

        last_attempted_rate = -1
        last_attempted_sample = -1

        while(1):
            # First, try to start at the rate specified.
            opened = False
            valid_config = False

            while not opened:
                try:
                    # Open a connection.
                    self.open(verbose=False)
                    scan_rate, sample_rate = self._setup(inputs,
                                                         inputs_max_voltages,
                                                         stream_setting,
                                                         resolution,
                                                         med_rate,
                                                         scans_per_read=scans_per_read)
                except Exception as e:
                    print(e)
                    if scans_per_read < med_rate:
                        # First, try increasing the number of elements/packet.
                        scans_per_read = min(2 * scans_per_read, med_rate)
                    else:
                        # Step down, and turn off exponential mode
                        exponential_mode = False
                        scans_per_read = last_good_scan_per_packet
                        max_rate = med_rate
                        med_rate = (min_rate + med_rate) / 2

                        if int(med_rate) == int(min_rate) \
                           or int(med_rate) == int(max_rate):
                            self._close_stream()
                            return last_good_rate - (last_good_rate % 100), \
                                last_good_scan_per_packet
                else:
                    opened = True

            # The below condition could happen when we are in binary search
            # mode, due to converging bounds.
            if last_attempted_rate == med_rate \
               and last_attempted_sample == scans_per_read \
               and not exponential_mode:

                # In all cases, we need to adjust the bounds of our search,
                # since we don't want to repeat any test we already did.
                if last_attempted_rate == last_good_rate \
                   and last_attempted_sample == last_good_scan_per_packet:
                    min_rate = med_rate
                else:
                    max_rate = med_rate

                # In all cases, adjust bounds and start from the top.
                med_rate = med_rate = (min_rate + max_rate) / 2
                continue

            last_attempted_rate = med_rate
            last_attempted_sample = scans_per_read

            iterations = 0
            buffer_size = 0
            num_skips = 0
            ljm_buffer_size = 0
            max_buffer_size = 0

            start = time.time()

            try:
                while time.time() - start < num_seconds:
                    # Read all rows of data off of the latest packet
                    # in the stream.
                    ret = ljm_reference.stream_read(self.handle)
                    buffer_size = ret[1]
                    ljm_buffer_size = max(ljm_buffer_size, ret[2])

                    for element in ret[0]:
                        if element == -9999.0:
                            num_skips += 1

                    max_buffer_size = max(max_buffer_size, buffer_size)
                    iterations += len(ret[0])

                    if buffer_size > MAX_BUFFERSIZE \
                       or num_skips \
                       or ljm_buffer_size > MAX_LJM_BUFFERSIZE:
                        if scans_per_read < med_rate:
                            # First, try increasing the number of elements
                            # per packet.
                            scans_per_read = min(2 * scans_per_read, med_rate)
                        else:
                            # Step down, and turn off exponential mode
                            scans_per_read = 1
                            max_rate = med_rate
                            med_rate = (min_rate + med_rate) / 2
                            exponential_mode = False

                            if (int(med_rate) == int(min_rate)
                               or int(med_rate) == int(max_rate)):
                                # Go to last good and terminate.
                                self._close_stream()
                                return last_good_rate - (last_good_rate % 100), \
                                    last_good_scan_per_packet

                        # In all cases, try again.
                        break

            except LJMError:
                if scans_per_read < med_rate:
                    # First, try increasing the number of elements per packet.
                    scans_per_read = min(2 * scans_per_read, med_rate)
                else:
                    # Step down, and turn off exponential mode
                    scans_per_read = last_good_scan_per_packet
                    max_rate = med_rate
                    med_rate = (min_rate + med_rate) / 2
                    exponential_mode = False
                    break
            else:
                if (buffer_size <= MAX_BUFFERSIZE
                   and not num_skips
                   and ljm_buffer_size <= MAX_LJM_BUFFERSIZE):
                    # Store these working values
                    last_good_rate = med_rate
                    last_good_scan_per_packet = scans_per_read
                    valid_config = True

                    if exponential_mode:
                        # Exponentially (const * 2^n) increase the upper
                        # search bound. Recalculate the midpoint.
                        min_rate = med_rate
                        max_rate = 2 * med_rate
                        med_rate = 1.5 * med_rate
                    else:
                        # Step up
                        min_rate = med_rate
                        med_rate = (max_rate + med_rate) / 2

                    if int(med_rate) == int(min_rate) \
                       or int(med_rate) == int(max_rate):
                        self._close_stream()
                        return last_good_rate - (last_good_rate % 100), \
                            last_good_scan_per_packet
            finally:
                self._close_stream()

                if verbose:
                    print("%s %14d [%7d %7d] %25s %25s %25s" %
                          ((Fore.GREEN + "[PASS]" if valid_config else Fore.RED + "[FAIL]") + Fore.RESET,
                           med_rate, min_rate, max_rate,
                           (Fore.RED if max_buffer_size > MAX_BUFFERSIZE else Fore.RESET) + str(max_buffer_size) + Fore.RESET,
                           (Fore.RED if ljm_buffer_size > MAX_LJM_BUFFERSIZE else Fore.RESET) + str(ljm_buffer_size) + Fore.RESET,
                           (Fore.RED if num_skips > 0 else Fore.RESET) + str(num_skips) + Fore.RESET))

    # TODO: stream_setting needs to be renamed and abstracted away, see STREAM_SETTLING_US
    # at https://labjack.com/support/datasheets/t-series/communication/stream-mode
    def collect_data(self,
                     inputs: List[str],
                     inputs_max_voltages: List[float],
                     seconds: float,
                     scan_rate: int,
                     scans_per_read=-1,
                     stream_setting=0,
                     resolution=8,
                     verbose=False) -> Tuple[float, float]:
        """
        Collect data from the LabJack device.

        Data collection will overwrite any data stored in this object's
        internal array.

        Parameters
        ----------
        inputs : sequence of strings
            Names of input channels on the LabJack device to read.
            Must correspond to the actual name on the device.
        inputs_max_voltages : sequence of real values
            Maximum voltages corresponding element-wise to the channels
            listed in inputs.
        seconds : float
            Duration of the data run in seconds. The run will last at least as
            long as this value, and will try to stop streaming when this time
            has been met.
        scan_rate : int
            Number of times per second (Hz) the device will get a data point
            for each of the channels specified.
        scans_per_read : int, optional
            Number of data points contained in a packet sent by the LabJack
            device. -1 indicates the maximum possible sample rate.
        stream_setting : int, optional
            See official LabJack documentation.
        resolution : int, optional
            See official LabJack documentation.
        verbose : str, optional
            If enabled, will print out statistics about each read.

        Returns
        -------
        tot_time : float
            The total amount of time actually spent collecting data
        num_skips : float
            The number of skipped data points.

        Examples
        --------
        Create a reader for a Labjack T7 and read off 60.5 seconds of data at
        50 kHz from channels AIN0, AIN1 which have a maximum voltage of 10V
        each:

        >>> reader = LabjackReader("T7")
        >>> reader.collect_data(["AIN0", "AIN1"], [10.0, 10.0], 60.5, 10000)
        None

        """

        if not len(inputs):
            raise ValueError("Needed a non-empty string collection of channels.")
        for channel in inputs:
            if not isinstance(channel, str):
                raise TypeError("Expected a string name for each channel,"
                                " not %s" % str(channel))

        # Input validation for inputs_max_voltages
        if not len(inputs_max_voltages):
            raise ValueError("Needed a non-empty numerical collection of values.")
        for channel in inputs:
            if not isinstance(channel, str):
                raise TypeError("Expected a numerical value, not %s"
                                % str(channel))

        if len(inputs_max_voltages) != len(inputs):
            raise ValueError("inputs must have the same length as "
                             "inputs_max_voltages, as they contain values that"
                             " correspond element-wise.")

        # Input validation for seconds
        if seconds <= 0:
            raise ValueError("Invalid duration for data collection.")

        # Input validation for scan_rate
        if scan_rate <= 0:
            raise ValueError("Invalid frequency provided for scan_rate.")

        # Open a connection.
        self.open(verbose=verbose)

        # Close the stream if it was already open; this is done
        # to prevent unexpected termination from last time messing
        # up the connection this time.
        self._close_stream()

        num_addrs = len(inputs)

        # Create a RawArray for multiple processes; this array
        # stores our data.
        size = int(seconds * scan_rate * (len(inputs) + 2))

        scan_rate, scans_per_read = self._setup(inputs, inputs_max_voltages,
                                                stream_setting, resolution,
                                                scan_rate,
                                                scans_per_read=scans_per_read)

        if verbose:
            print("[%26s] %15s / %15s %5s  %15s %15s"
                  % ("Time", "Max Index", "Total Indices", "%",
                     "Scans on Device", "Scans on LJM"))

        self.input_channels = inputs

        total_skip = 0  # Total skipped samples

        packet_num = 0
        self.max_index = 0
        step_size = len(inputs)

        self.data_arr = (ctypes.c_double * size)(size)

        start = time_func()
        while self.max_index < size:
            # Read all rows of data off of the latest packet in the stream.
            ret = ljm_reference.stream_read(self.handle)
            curr_data = ret[0]

            if verbose:
                print("[%26s] %15d / %15d %4.1d%% %15d %15d"
                      % (datetime.datetime.now(), self.max_index, size,
                         ((float(self.max_index) / float(size)) * 100 if self.max_index else 0), ret[1], ret[2]))

            for i in range(0, len(curr_data), step_size):
                # Ensure that this packet won't overflow our buffer.
                if self._max_index >= size:
                    break

                # We will manually calculate the times each entry occurs at.
                # The stream itself is timed by the same clock that runs
                # CORE_TIMER, and it is officially advised we use the stream
                # clocking instead.
                # See https://forums.labjack.com/index.php?showtopic=6992
                curr_time = (scans_per_read / scan_rate) * (packet_num + (i / len(curr_data)))

                # We get a giant 1D list back, so work with what we have.
                self.data_arr[self._max_index: self._max_index + step_size] =\
                    curr_data[i:i + step_size]
                self._max_index += step_size

                # Put in the time as well
                self.data_arr[self._max_index] = curr_time
                self._max_index += 1
                self.data_arr[self._max_index] = time_func() - start
                self._max_index += 1

            packet_num += 1

            # Count the skipped samples which are indicated by -9999 values
            # Missed samples occur after a device's stream buffer overflows
            # and are reported after auto-recover mode ends.
            curr_skip = 0
            for value in curr_data:
                if value == -9999.0:
                    curr_skip += 1

            total_skip += curr_skip

            ainStr = ""
            for j in range(0, num_addrs):
                ainStr += "%s = %0.5f, " % (inputs[j], curr_data[j])
            if curr_skip:
                print("Scans Skipped = %0.0f" % (curr_skip/num_addrs))

        # We are done, record the actual ending time.
        end = time_func()

        total_time = end - start
        if verbose:
            print("\nTotal scans = %i\n"
                  "Time taken = %f seconds\n"
                  "LJM Scan Rate = %f scans/second\n"
                  "Timed Scan Rate = %f scans/second\n"
                  "Timed Sample Rate = %f samples/second\n"
                  "Skipped scans = %0.0f"
                  % (self.max_index, total_time, scan_rate,
                     (self.max_index / total_time),
                     (self.max_index * num_addrs / total_time),
                     (total_skip / num_addrs)))

        # Close the connection.
        self._close_stream()

        return total_time, (total_skip / num_addrs)

    def to_list(self, mode="all", **kwargs) -> Union[List[List[float]], None]:
        """
        Return data in latest array.

        Parameters
        ----------
        mode: str, optional
            Valid options are
            'all': Get all data.
            'relative': Expects you to use the kwarg num_rows=n, with n as the
                        number of rows to retrieve relative to the end.
            'range': Retrieves a range of rows. Expects the kwargs 'start'
                     and 'end'.

        Returns
        -------
        array_like: ndarray
            A 2D array in the shape (ceil(1d data len/ (number of channels + 2),
                                     number of channels + 2)
            Final two columns are the LabJack device's time in nanoseconds, and
            the host system's time, also in nanoseconds.

        Examples
        --------
        Create a reader for a Labjack T7 and read off 60.5 seconds of data at
        50 kHz from channels AIN0, AIN1 which have a maximum voltage of 10V
        each.

        >>> reader = LabjackReader("T7")
        >>> reader.collect_data(["AIN0", "AIN1"], [10.0, 10.0], 60.5, 10000)
        >>> # Return all the data we collected.
        >>> reader.to_list(mode='all')
        [[.....]
         [.....]
         [.....]
        ...
        [.....]
        [.....]
        [.....]]
        >>> # Return the last 50 rows of data we collected.
        >>> reader.to_list(mode='relative', num_rows=50)
        [[.....]
         [.....]
         [.....]
        ...
        [.....]
        [.....]
        [.....]]
        >>> # Return rows 17 through 65, inclusive, of the collected data.
        >>> reader.to_list(mode='range', start=17, end=65)
        [[.....]
         [.....]
         [.....]
        ...
        [.....]
        [.....]
        [.....]]

        Notes
        -----
        If the internal data array has not been initialized yet, the return
        value of this function will be None.
        """
        max_row = self.max_index
        if max_row < 0:
            return None

        row_width = len(self.input_channels) + 2
        max_row = int(max_row / row_width)

        if mode == "all" or mode == 'all':
            return self._reshape_data(0, max_row)
        elif mode == "range" or mode == 'range':
            if "start" in kwargs and "end" in kwargs:
                from_range, to_range = kwargs["start"], kwargs["end"]
                if 0 <= from_range < to_range and to_range < max_row:
                    return self._reshape_data(from_range, to_range)
                else:
                    raise Exception("Invalid range provided of [%d, %d]"
                                    % (from_range, to_range))
            else:
                raise Exception("The kwargs \"start\" and \"end\" must"
                                " be specified in range mode.")
        elif mode == "relative" or mode == 'relative':
            if "num_rows" in kwargs:
                if kwargs["num_rows"] < 0 or kwargs["num_rows"] > max_row:
                    raise Exception("Invalid number of rows provided")
                else:
                    return self._reshape_data(max_row - kwargs["num_rows"],
                                              max_row)
            else:
                raise Exception("Number of rows must be specified in"
                                " relative mode.")

    def to_dataframe(self, mode="all", **kwargs):
        """
        Gets this object's recorded data in dataframe form.

        Parameters
        ----------
        mode: str, optional
            Valid options are
            'all': Get all data.
            'relative': Expects you to use the kwarg num_rows=n, with n as the
                        number of rows to retrieve relative to the end.
            'range': Retrieves a range of rows. Expects the kwargs 'start'
                     and 'end'.

        Returns
        -------
        table: dataframe
            A Pandas Dataframe with the following columns:
            AINB....AINC: Voltage values for the user-specified channels
                          AIN #B to #C.
            Time:  Recorded time (in nanoseconds) of datapoints in row, as
                   observed by the LabJack
            System Time: Recorded time (in nanoseconds) of datapoints in
                         row, as observed by the host computer

        Notes
        -----
        If the internal data array has not been initialized yet, behavior
        is undefined.
        """

        return pd.DataFrame(self.to_list(mode, **kwargs),
                            columns=self.input_channels
                            + ["Time", "System Time"])
