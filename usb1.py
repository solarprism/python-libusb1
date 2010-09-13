"""
Pythonic wrapper for libusb-1.0.

The first thing you must do is to get an "USB context". To do so, create a
LibUSBContext instance.
Then, you can use it to browse available USB devices and open the one you want
to talk to.
At this point, you should have a USBDeviceHandle instance (as returned by
LibUSBContext or USBDevice instances), and you can start exchanging with the
device.

Features:
- Basic device settings (configuration & interface selection, ...)
- String descriptor lookups (ASCII & unicode), and list supported language
  codes
- Synchronous I/O (control, bulk, interrupt)
  Note: Isochronous support is not implemented yet.
- Asyncrhonous I/O (control, bulk, interrupt)
  Note: Isochronous support is not implemented yet.
  See USBPoller, USBTransfer and USBTransferHelper.
"""

import libusb1
from ctypes import byref, create_string_buffer, c_int, sizeof, POINTER, \
    create_unicode_buffer, c_wchar, cast, c_uint16, c_ubyte, string_at, \
    addressof
from cStringIO import StringIO

__all__ = ['LibUSBContext', 'USBDeviceHandle', 'USBDevice',
    'USBPoller', 'USBTransfer', 'USBTransferHelper', 'EVENT_CALLBACK_SET']

# Default string length
# From a comment in libusb-1.0: "Some devices choke on size > 255"
STRING_LENGTH = 255

EVENT_CALLBACK_SET = frozenset((
  libusb1.LIBUSB_TRANSFER_COMPLETED,
  libusb1.LIBUSB_TRANSFER_ERROR,
  libusb1.LIBUSB_TRANSFER_TIMED_OUT,
  libusb1.LIBUSB_TRANSFER_CANCELLED,
  libusb1.LIBUSB_TRANSFER_STALL,
  libusb1.LIBUSB_TRANSFER_NO_DEVICE,
  libusb1.LIBUSB_TRANSFER_OVERFLOW,
))

DEFAULT_ASYNC_TRANSFER_ERROR_CALLBACK = lambda x, y: False

class USBTransfer(object):
    """
    USB asynchronous transfer control & data.

    All modification methods will raise if called on a submitted transfer.
    Methods noted as "should not be called on a submitted transfer" will not
    prevent you from reading, but returned value is unspecified.
    """
    # Prevent garbage collector from freeing the free function before our
    # instances, as we need it to property destruct them.
    __libusb_free_transfer = libusb1.libusb_free_transfer
    __transfer = None
    __initialized = False
    __submitted = False
    __callback = None
    __ctypesCallbackWrapper = None

    def __init__(self, handle, iso_packets=0):
        """
        You should not instanciate this class directly.
        Call "getTransfer" method on an USBDeviceHandle instance to get
        instances of this class.
        """
        self.__handle = handle
        result = libusb1.libusb_alloc_transfer(iso_packets)
        if not result:
            raise libusb1.USBError, 'Unable to get a transfer object'
        self.__transfer = result
        self.__ctypesCallbackWrapper = libusb1.libusb_transfer_cb_fn_p(
            self.__callbackWrapper)

    def close(self):
        """
        Stop using this transfer.
        This removes some references to other python objects, to help garbage
        collection.
        Raises if called on a submitted transfer.
        This does not prevent future reuse of instance (calling one of
        "setControl", "setBulk", "setInterrupt" or "setIsochronous" methods
        will initialize it properly again), just makes it ready to be
        garbage-collected.
        It is not mandatory to call it either, if you have no problems with
        garbage collection.
        """
        if self.__submitted:
            raise ValueError, 'Cannot close a submitted transfer'
        self.__initialized = False
        self.__callback = None

    def __del__(self):
        if self.__transfer is not None:
            try:
                try:
                    self.cancel()
                except libusb1.USBError, exception:
                    if exception.value != libusb1.LIBUSB_ERROR_NOT_FOUND:
                        raise
            finally:
                self.__libusb_free_transfer(self.__transfer)

    def __callbackWrapper(self, transfer_p):
        """
        Makes it possible for user-provided callback to alter transfer when
        fired (ie, mark transfer as not submitted upon call).
        """
        mine = addressof(self.__transfer.contents)
        his = addressof(transfer_p.contents)
        assert mine == his, (mine, his)
        self.__submitted = False
        self.__callback(self)

    def setCallback(self, callback):
        """
        Change transfer's callback.
        """
        if self.__submitted:
            raise ValueError, 'Cannot alter a submitted transfer'
        self.__callback = callback

    def getCallback(self):
        """
        Get currently set callback.
        """
        return self.__callback

    def setControl(self, request_type, request, value, index, buffer_or_len,
            callback=None, user_data=None, timeout=0):
        """
        Setup transfer for control use.

        request_type, request, value, index: See USBDeviceHandle.controlWrite.
        buffer_or_len: either a string (when sending data), or expected data
          length (when receiving data)
        callback: function to call upon event. Called with transfer as
          parameter, return value ignored.
        user_data: to pass some data to/from callback
        timeout: in milliseconds, how long to wait for devices acknowledgement
          or data. Set to 0 to disable.
        """
        if self.__submitted:
            raise ValueError, 'Cannot alter a submitted transfer'
        if isinstance(buffer_or_len, basestring):
            length = len(buffer_or_len)
            string_buffer = create_string_buffer(
                ' ' * libusb1.LIBUSB_CONTROL_SETUP_SIZE + buffer_or_len)
        else:
            length = buffer_or_len
            string_buffer = create_string_buffer(length + \
                libusb1.LIBUSB_CONTROL_SETUP_SIZE)
        libusb1.libusb_fill_control_setup(string_buffer, request_type,
            request, value, index, length)
        libusb1.libusb_fill_control_transfer(self.__transfer, self.__handle,
            string_buffer, self.__ctypesCallbackWrapper, user_data, timeout)
        self.__callback = callback
        self.__initialized = True

    def setBulk(self, endpoint, buffer_or_len, callback=None, user_data=None,
            timeout=0):
        """
        Setup transfer for bulk use.

        endpoint: endpoint to submit transfer to (implicitly sets transfer
          direction).
        buffer_or_len: either a string (when sending data), or expected data
          length (when receiving data)
        callback: function to call upon event. Called with transfer as
          parameter, return value ignored.
        user_data: to pass some data to/from callback
        timeout: in milliseconds, how long to wait for devices acknowledgement
          or data. Set to 0 to disable.
        """
        if self.__submitted:
            raise ValueError, 'Cannot alter a submitted transfer'
        string_buffer = create_string_buffer(buffer_or_len)
        libusb1.libusb_fill_bulk_transfer(self.__transfer, self.__handle,
            endpoint, string_buffer, sizeof(string_buffer),
            self.__ctypesCallbackWrapper, user_data, timeout)
        self.__callback = callback
        self.__initialized = True

    def setInterrupt(self, endpoint, buffer_or_len, callback=None,
            user_data=None, timeout=0):
        """
        Setup transfer for interrupt use.

        endpoint: endpoint to submit transfer to (implicitly sets transfer
          direction).
        buffer_or_len: either a string (when sending data), or expected data
          length (when receiving data)
        callback: function to call upon event. Called with transfer as
          parameter, return value ignored.
        user_data: to pass some data to/from callback
        timeout: in milliseconds, how long to wait for devices acknowledgement
          or data. Set to 0 to disable.
        """
        if self.__submitted:
            raise ValueError, 'Cannot alter a submitted transfer'
        string_buffer = create_string_buffer(buffer_or_len)
        libusb1.libusb_fill_interrupt_transfer(self.__transfer, self.__handle,
            endpoint, string_buffer,  sizeof(string_buffer),
            self.__ctypesCallbackWrapper, user_data, timeout)
        self.__callback = callback
        self.__initialized = True

    def setIsochronous(self, *args, **kw):
        """
        Setup transfer for isochronous use.
        XXX: Not implemented yet
        """
        if self.__submitted:
            raise ValueError, 'Cannot alter a submitted transfer'
        raise NotImplementedError

    def getStatus(self):
        """
        Get transfer status.
        Should not be called on a submitted transfer.
        """
        return self.__transfer.contents.status

    def getActualLength(self):
        """
        Get actually transfered data length.
        Should not be called on a submitted transfer.
        """
        return self.__transfer.contents.actual_length

    def getBuffer(self):
        """
        Get data buffer content.
        Should not be called on a submitted transfer.
        """
        transfer = self.__transfer
        if transfer.type == libusb1.LIBUSB_TRANSFER_TYPE_CONTROL:
            result = libusb1.libusb_control_transfer_get_data(transfer)
        else:
            result = string_at(transfer.contents.buffer, transfer.length)
        return result

    def setBuffer(self, buffer_or_len):
        """
        Replace buffer with a new one.
        Allows resizing read buffer and replacing data sent.
        """
        if self.__submitted:
            raise ValueError, 'Cannot alter a submitted transfer'
        transfer = self.__transfer.contents
        if transfer.type == libusb1.LIBUSB_TRANSFER_TYPE_CONTROL:
            setup = string_at(transfer.buffer,
                libusb1.LIBUSB_CONTROL_SETUP_SIZE)
            if isinstance(buffer_or_len, basestring):
                wLength = len(buffer_or_len)
                string_buffer = create_string_buffer(setup + buffer_or_len)
            else:
                wLength = buffer_or_len
                string_buffer = create_string_buffer(setup, buffer_or_len + \
                    libusb1.LIBUSB_CONTROL_SETUP_SIZE)
            cast(string_buffer, libusb1.libusb_control_setup_p).contents.\
                wLength = wLength
        else:
            string_buffer = create_string_buffer(buffer_or_len)
        transfer.buffer = string_buffer
        transfer.length = sizeof(string_buffer)

    def isSubmitted(self):
        """
        Tells if this transfer is submitted and still pending.
        """
        return self.__submitted

    def submit(self):
        """
        Submit a transfer for asynchronous handling.
        """
        if not self.__initialized:
            raise ValueError, 'Cannot submit a transfer until it has been ' \
                'initialized'
        if self.__callback is None:
            raise ValueError, 'A callback must be set on transfer before it ' \
                'can be submitted'
        result = libusb1.libusb_submit_transfer(self.__transfer)
        if result:
            raise libusb1.USBError, result
        self.__submitted = True

    def cancel(self):
        """
        Cancel given transfer.
        Note: cancellation happensasynchronously, so you must wait for
        LIBUSB_TRANSFER_CANCELLED.
        """
        result = libusb1.libusb_cancel_transfer(self.__transfer)
        if result:
            raise libusb1.USBError, result
        self.__submitted = False

class USBTransferHelper(object):
    """
    Simplifies subscribing to the same transfer over and over, and callback
    handling.

    Callbacks used in this class must follow the callback API described in
    USBTransfer, and are expected to return a boolean:
    - True if transfer is to be submitted again (to receive/send more data)
    - False otherwise
    """
    def __init__(self, transfer):
        """
        Create a helper for given USBTransfer.
        Note: transfer's callback function is overwritten upon instanciation.
        """
        self.__transfer = transfer
        transfer.setCallback(self.__callbackDispatcher)
        self.__event_callback_dict = {}
        self.__errorCallback = DEFAULT_ASYNC_TRANSFER_ERROR_CALLBACK

    def submit(self):
        """
        Submit the asynchronous read request.
        """
        self.__transfer.submit()

    def cancel(self):
        """
        Cancel a pending read request.
        """
        self.__transfer.cancel()

    def setEventCallback(self, event, callback):
        """
        Set a function to call for a given event.
        Possible event identifiers are listed in EVENT_CALLBACK_SET.
        """
        if event not in EVENT_CALLBACK_SET:
            raise ValueError, 'Unknown event %r.' % (event, )
        self.__event_callback_dict[event] = callback

    def setDefaultCallback(self, callback):
        """
        Set the function to call for event which don't have a specific callback
        registered.
        The initial default callback does nothing and returns false.
        """
        self.__errorCallback = callback

    def getEventCallback(self, event, default=None):
        """
        Return the function registered to be called for given event identifier.
        """
        return self.__event_callback_dict.get(event, default)

    def __callbackDispatcher(self, transfer):
        assert transfer is self.__transfer
        if self.getEventCallback(transfer.getStatus(), self.__errorCallback)(
                transfer):
            self.submit()

    def isSubmited(self):
        """
        Returns whether this reader is currently waiting for an event.
        """
        return self.__transfer.isSubmitted()

    def __del__(self):
        try:
            self.cancel()
        except libusb1.USBError, exception:
            if exception.value != libusb1.LIBUSB_ERROR_NOT_FOUND:
                raise

class USBPoller(object):
    """
    Class allowing integration of USB event polling in a file-descriptor
    monitoring event loop.
    """
    def __init__(self, context, poller):
        """
        Create a poller for given context.
        Warning: it will not check if another poller instance was already
        present for that context, and will replace it.

        poller is a polling instance implementing the follwing methods:
        - register(fd, event_flags)
          event_flags have the same meaning as in poll API (POLLIN & POLLOUT)
        - unregister(fd)
        - poll(timeout)
          timeout being a float in seconds, or None if there is no timeout.
          It must return a list of pairs, in which the first event must be the
          file descriptor on which an event happened.
        """
        self.__context = context
        self.__poller = poller
        self.__fd_set = set()
        context.setPollFDNotifiers(self._registerFD, self._unregisterFD)
        for fd, events in context.getPollFDList():
            self._registerFD(fd, events)

    def __del__(self):
        self.__context.setPollFDNotifiers(None, None)

    def poll(self, timeout=None):
        """
        Poll for events.
        timeout can be a float in seconds, or None for no timeout.
        Returns a list of (descriptor, event) pairs.
        """
        fd_set = self.__fd_set
        next_usb_timeout = self.__context.getNextTimeout()
        if timeout is None:
            usb_timeout = next_usb_timeout
        else:
            usb_timeout = min(next_usb_timeout or timeout, timeout)
        event_list = self.__poller.poll(usb_timeout)
        event_list_len = len(event_list)
        if event_list_len:
            result = [(x, y) for x, y in event_list if x not in fd_set]
            if len(result) != event_list_len:
                self.__context.handleEventsTimeout()
        else:
            result = event_list
            self.__context.handleEventsTimeout()
        return result

    def register(self, fd, events):
        """
        Register an USB-unrelated fd to poller.
        Convenience method.
        """
        self.__poller.register(fd, events)

    def unregister(self, fd):
        """
        Unregister an USB-unrelated fd from poller.
        Convenience method.
        """
        self.__poller.unregister(fd)

    def _registerFD(self, fd, events, user_data=None):
        self.__fd_set.add(fd)
        self.register(fd, events)

    def _unregisterFD(self, fd, user_data=None):
        self.unregister(fd)
        self.__fd_set.discard(fd)

class USBDeviceHandle(object):
    """
    Represents an opened USB device.
    """
    __handle = None

    def __init__(self, context, handle):
        """
        You should not instanciate this class directly.
        Call "open" method on an USBDevice instance to get an USBDeviceHandle
        instance.
        """
        # XXX Context parameter is just here as a hint for garbage collector:
        # It must collect USBDeviceHandle instance before their LibUSBContext.
        self.__context = context
        self.__handle = handle

    def __del__(self):
        self.close()

    def close(self):
        """
        Close this handle. If not called explicitely, will be called by
        destructor.
        """
        handle = self.__handle
        if handle is not None:
            libusb1.libusb_close(handle)
            self.__handle = None

    def getConfiguration(self):
        """
        Get the current configuration number for this device.
        """
        configuration = c_int()
        result = libusb1.libusb_get_configuration(self.__handle,
                                                  byref(configuration))
        if result:
            raise libusb1.USBError, result
        return configuration

    def setConfiguration(self, configuration):
        """
        Set the configuration number for this device.
        """
        result = libusb1.libusb_set_configuration(self.__handle, configuration)
        if result:
            raise libusb1.USBError, result

    def claimInterface(self, interface):
        """
        Claim (= get exclusive access to) given interface number. Required to
        receive/send data.
        """
        result = libusb1.libusb_claim_interface(self.__handle, interface)
        if result:
            raise libusb1.USBError, result

    def releaseInterface(self, interface):
        """
        Release interface, allowing another process to use it.
        """
        result = libusb1.libusb_release_interface(self.__handle, interface)
        if result:
            raise libusb1.USBError, result

    def setInterfaceAltSetting(self, interface, alt_setting):
        """
        Set interface's alternative setting (both parameters are integers).
        """
        result = libusb1.libusb_set_interface_alt_setting(self.__handle,
                                                          interface,
                                                          alt_setting)
        if result:
            raise libusb1.USBError, result

    def clearHalt(self, endpoint):
        """
        Clear a halt state on given endpoint number.
        """
        result = libusb1.libusb_clear_halt(self.__handle, endpoint)
        if result:
            raise libusb1.USBError, result

    def resetDevice(self):
        """
        Reinitialise current device.
        Attempts to restore current configuration & alt settings.
        If this fails, will result in a device diconnect & reconnect, so you
        have to close current device and rediscover it (notified by a
        LIBUSB_ERROR_NOT_FOUND error code).
        """
        result = libusb1.libusb_reset_device(self.__handle)
        if result:
            raise libusb1.USBError, result

    def kernelDriverActive(self, interface):
        """
        Tell whether a kernel driver is active on given interface number.
        """
        result = libusb1.libusb_kernel_driver_active(self.__handle, interface)
        if result == 0:
            is_active = False
        elif result == 1:
            is_active = True
        else:
            raise libusb1.USBError, result
        return is_active

    def detachKernelDriver(self, interface):
        """
        Ask kernel driver to detach from given interface number.
        """
        result = libusb1.libusb_detach_kernel_driver(self.__handle, interface)
        if result:
            raise libusb1.USBError, result

    def attachKernelDriver(self, interface):
        """
        Ask kernel driver to re-attach to given interface number.
        """
        result = libusb1.libusb_attach_kernel_driver(self.__handle, interface)
        if result:
            raise libusb1.USBError, result

    def getSupportedLanguageList(self):
        """
        Return a list of USB language identifiers (as integers) supported by
        current device for its string descriptors.
        """
        descriptor_string = create_string_buffer(STRING_LENGTH)
        result = libusb1.libusb_get_string_descriptor(self.__handle,
            0, 0, descriptor_string, sizeof(descriptor_string))
        if result < 0:
            if result == libusb1.LIBUSB_ERROR_PIPE:
                # From libusb_control_transfer doc:
                # control request not supported by the device
                return []
            raise libusb1.USBError, result
        length = cast(descriptor_string, POINTER(c_ubyte))[0]
        langid_list = cast(descriptor_string, POINTER(c_uint16))
        result = []
        append = result.append
        for offset in xrange(1, length / 2):
            append(libusb1.libusb_le16_to_cpu(langid_list[offset]))
        return result

    def getStringDescriptor(self, descriptor, lang_id):
        """
        Fetch description string for given descriptor and in given language.
        Use getSupportedLanguageList to know which languages are available.
        Return value is an unicode string.
        """
        descriptor_string = create_unicode_buffer(
            STRING_LENGTH / sizeof(c_wchar))
        result = libusb1.libusb_get_string_descriptor(self.__handle,
            descriptor, lang_id, descriptor_string, sizeof(descriptor_string))
        if result < 0:
            raise libusb1.USBError, result
        return descriptor_string.value

    def getASCIIStringDescriptor(self, descriptor):
        """
        Fetch description string for given descriptor in first available
        language.
        Return value is an ASCII string.
        """
        descriptor_string = create_string_buffer(STRING_LENGTH)
        result = libusb1.libusb_get_string_descriptor_ascii(self.__handle,
             descriptor, descriptor_string, sizeof(descriptor_string))
        if result < 0:
            raise libusb1.USBError, result
        return descriptor_string.value

    # Sync I/O

    def _controlTransfer(self, request_type, request, value, index, data,
                         length, timeout):
        result = libusb1.libusb_control_transfer(self.__handle, request_type,
            request, value, index, data, length, timeout)
        if result < 0:
            raise libusb1.USBError, result
        return result

    def controlWrite(self, request_type, request, value, index, data,
                     timeout=0):
        """
        Synchronous control write.
        request_type: request type bitmask (bmRequestType), see libusb1
          constants LIBUSB_TYPE_* and LIBUSB_RECIPIENT_*.
        request: request id (some values are standard).
        value, index, data: meaning is request-dependent.
        timeout: in milliseconds, how long to wait for device acknowledgement.
          Set to 0 to disable.

        Returns the number of bytes actually sent.
        """
        request_type = (request_type & ~libusb1.USB_ENDPOINT_DIR_MASK) | \
                        libusb1.LIBUSB_ENDPOINT_OUT
        data = create_string_buffer(data)
        return self._controlTransfer(request_type, request, value, index, data,
                                     len(data)-1, timeout)

    def controlRead(self, request_type, request, value, index, length,
                    timeout=0):
        """
        Syncrhonous control read.
        timeout: in milliseconds, how long to wait for data. Set to 0 to
          disable.
        See controlWrite for other parameters description.
        """
        request_type = (request_type & ~libusb1.USB_ENDPOINT_DIR_MASK) | \
                        libusb1.LIBUSB_ENDPOINT_IN
        data = create_string_buffer(length)
        transferred = self._controlTransfer(request_type, request, value,
                                            index, data, length, timeout)
        return data.raw[:transferred]

    def _bulkTransfer(self, endpoint, data, length, timeout):
        transferred = c_int()
        result = libusb1.libusb_bulk_transfer(self.__handle, endpoint,
            data, length, byref(transferred), timeout)
        if result:
            raise libusb1.USBError, result
        return transferred.value

    def bulkWrite(self, endpoint, data, timeout=0):
        """
        Syncrhonous bulk write.
        endpoint: endpoint to send data to.
        data: data to send.
        timeout: in milliseconds, how long to wait for device acknowledgement.
          Set to 0 to disable.
        """
        endpoint = (endpoint & ~libusb1.USB_ENDPOINT_DIR_MASK) | \
                    libusb1.LIBUSB_ENDPOINT_OUT
        data = create_string_buffer(data)
        return self._bulkTransfer(endpoint, data, len(data) - 1, timeout)

    def bulkRead(self, endpoint, length, timeout=0):
        """
        Syncrhonous bulk read.
        timeout: in milliseconds, how long to wait for data. Set to 0 to
          disable.
        See bulkWrite for other parameters description.
        """
        endpoint = (endpoint & ~libusb1.USB_ENDPOINT_DIR_MASK) | \
                    libusb1.LIBUSB_ENDPOINT_IN
        data = create_string_buffer(length)
        transferred = self._bulkTransfer(endpoint, data, length, timeout)
        return data.raw[:transferred]

    def _interruptTransfer(self, endpoint, data, length, timeout):
        transferred = c_int()
        result = libusb1.libusb_interrupt_transfer(self.__handle, endpoint,
            data, length, byref(transferred), timeout)
        if result:
            raise libusb1.USBError, result
        return transferred.value

    def interruptWrite(self, endpoint, data, timeout=0):
        """
        Synchronous interrupt write.
        endpoint: endpoint to send data to.
        data: data to send.
        timeout: in milliseconds, how long to wait for device acknowledgement.
          Set to 0 to disable.
        """
        endpoint = (endpoint & ~libusb1.USB_ENDPOINT_DIR_MASK) | \
                    libusb1.LIBUSB_ENDPOINT_OUT
        data = create_string_buffer(data)
        return self._interruptTransfer(endpoint, data, len(data) - 1, timeout)

    def interruptRead(self, endpoint, length, timeout=0):
        """
        Synchronous interrupt write.
        timeout: in milliseconds, how long to wait for data. Set to 0 to
          disable.
        See interruptRead for other parameters description.
        """
        endpoint = (endpoint & ~libusb1.USB_ENDPOINT_DIR_MASK) | \
                    libusb1.LIBUSB_ENDPOINT_IN
        data = create_string_buffer(length)
        transferred = self._interruptTransfer(endpoint, data, length, timeout)
        return data.raw[:transferred]

    def getTransfer(self, iso_packets=0):
        """
        Get an empty transfer for asynchronous use.
        iso_packets: the number of isochronous transfer descriptors to
          allocate.
        """
        return USBTransfer(self.__handle, iso_packets)

class USBDevice(object):
    """
    Represents a USB device.
    """

    __configuration_descriptor_list = None

    def __init__(self, context, device_p):
        """
        You should not instanciate this class directly.
        Call LibUSBContext methods to receive instances of this class.
        """
        self.__context = context
        libusb1.libusb_ref_device(device_p)
        self.device_p = device_p
        # Fetch device descriptor
        device_descriptor = libusb1.libusb_device_descriptor()
        result = libusb1.libusb_get_device_descriptor(device_p,
            byref(device_descriptor))
        if result:
            raise libusb1.USBError, result
        self.device_descriptor = device_descriptor
        # Fetch all configuration descriptors
        self.__configuration_descriptor_list = []
        append = self.__configuration_descriptor_list.append
        for configuration_id in xrange(device_descriptor.bNumConfigurations):
            config = libusb1.libusb_config_descriptor_p()
            result = libusb1.libusb_get_config_descriptor(device_p,
                configuration_id, byref(config))
            if result:
                raise libusb1.USBError, result
            append(config.contents)

    def __del__(self):
        libusb1.libusb_unref_device(self.device_p)
        if self.__configuration_descriptor_list is not None:
            for config in self.__configuration_descriptor_list:
                libusb1.libusb_free_config_descriptor(byref(config))

    def __str__(self):
        return 'Bus %03i Device %03i: ID %04x:%04x %s %s' % (
            self.getBusNumber(),
            self.getDeviceAddress(),
            self.getVendorID(),
            self.getProductID(),
            self.getManufacturer(),
            self.getProduct()
        )

    def reprConfigurations(self):
        """
        Get a string representation of device's configurations.
        Note: opens the device temporarily.
        """
        out = StringIO()
        for config in self.__configuration_descriptor_list:
            print >> out, 'Configuration %i: %s' % (config.bConfigurationValue,
                self._getASCIIStringDescriptor(config.iConfiguration))
            print >> out, '  Max Power: %i mA' % (config.MaxPower * 2, )
            # TODO: bmAttributes dump
            for interface_num in xrange(config.bNumInterfaces):
                interface = config.interface[interface_num]
                print >> out, '  Interface %i' % (interface_num, )
                for alt_setting_num in xrange(interface.num_altsetting):
                    altsetting = interface.altsetting[alt_setting_num]
                    print >> out, '    Alt Setting %i: %s' % (alt_setting_num,
                        self._getASCIIStringDescriptor(altsetting.iInterface))
                    print >> out, '      Class: %02x Subclass: %02x' % \
                        (altsetting.bInterfaceClass,
                         altsetting.bInterfaceSubClass)
                    print >> out, '      Protocol: %02x' % \
                        (altsetting.bInterfaceProtocol, )
                    for endpoint_num in xrange(altsetting.bNumEndpoints):
                        endpoint = altsetting.endpoint[endpoint_num]
                        print >> out, '      Endpoint %i' % (endpoint_num, )
                        print >> out, '        Address: %02x' % \
                            (endpoint.bEndpointAddress, )
                        attribute_list = []
                        transfer_type = endpoint.bmAttributes & \
                            libusb1.LIBUSB_TRANSFER_TYPE_MASK
                        attribute_list.append(libusb1.libusb_transfer_type(
                            transfer_type
                        ))
                        if transfer_type == \
                            libusb1.LIBUSB_TRANSFER_TYPE_ISOCHRONOUS:
                            attribute_list.append(libusb1.libusb_iso_sync_type(
                                (endpoint.bmAttributes & \
                                 libusb1.LIBUSB_ISO_SYNC_TYPE_MASK) >> 2
                            ))
                            attribute_list.append(libusb1.libusb_iso_usage_type(
                                (endpoint.bmAttributes & \
                                 libusb1.LIBUSB_ISO_USAGE_TYPE_MASK) >> 4
                            ))
                        print >> out, '        Attributes: %s' % \
                            (', '.join(attribute_list), )
                        print >> out, '        Max Packet Size: %i' % \
                            (endpoint.wMaxPacketSize, )
                        print >> out, '        Interval: %i' % \
                            (endpoint.bInterval, )
                        print >> out, '        Refresh: %i' % \
                            (endpoint.bRefresh, )
                        print >> out, '        Sync Address: %02x' % \
                            (endpoint.bSynchAddress, )
        return out.getvalue()

    def getBusNumber(self):
        """
        Get device's bus number.
        """
        return libusb1.libusb_get_bus_number(self.device_p)

    def getDeviceAddress(self):
        """
        Get device's address on its bus.
        """
        return libusb1.libusb_get_device_address(self.device_p)

    def getbcdUSB(self):
        """
        Get the USB spec version device complies to, in BCD format.
        """
        return self.device_descriptor.bcdUSB

    def getDeviceClass(self):
        """
        Get device's class id.
        """
        return self.device_descriptor.bDeviceClass

    def getDeviceSubClass(self):
        """
        Get device's subclass id.
        """
        return self.device_descriptor.bDeviceSubClass

    def getDeviceProtocol(self):
        """
        Get device's protocol id.
        """
        return self.device_descriptor.bDeviceProtocol

    def getMaxPacketSize0(self):
        """
        Get device's max packet size for endpoint 0 (control).
        """
        return self.device_descriptor.bMaxPacketSize0

    def getVendorID(self):
        """
        Get device's vendor id.
        """
        return self.device_descriptor.idVendor

    def getProductID(self):
        """
        Get device's product id.
        """
        return self.device_descriptor.idProduct

    def getbcdDevice(self):
        """
        Get device's release number.
        """
        return self.device_descriptor.bcdDevice

    def getSupportedLanguageList(self):
        """
        Get the list of language ids device has string descriptors for.
        """
        temp_handle = self.open()
        return temp_handle.getSupportedLanguageList()

    def _getStringDescriptor(self, descriptor, lang_id):
        if descriptor == 0:
            result = None
        else:
            temp_handle = self.open()
            result = temp_handle.getStringDescriptor(descriptor, lang_id)
        return result

    def _getASCIIStringDescriptor(self, descriptor):
        if descriptor == 0:
            result = None
        else:
            temp_handle = self.open()
            result = temp_handle.getASCIIStringDescriptor(descriptor)
        return result

    def getManufacturer(self):
        """
        Get device's manufaturer name.
        Note: opens the device temporarily.
        """
        return self._getASCIIStringDescriptor(
            self.device_descriptor.iManufacturer)

    def getProduct(self):
        """
        Get device's product name.
        Note: opens the device temporarily.
        """
        return self._getASCIIStringDescriptor(self.device_descriptor.iProduct)

    def getSerialNumber(self):
        """
        Get device's serial number.
        Note: opens the device temporarily.
        """
        return self._getASCIIStringDescriptor(
            self.device_descriptor.iSerialNumber)

    def getNumConfigurations(self):
        """
        Get device's number of possible configurations.
        """
        return self.device_descriptor.bNumConfigurations

    def open(self):
        """
        Open device.
        Returns an USBDeviceHandler instance.
        """
        handle = libusb1.libusb_device_handle_p()
        result = libusb1.libusb_open(self.device_p, byref(handle))
        if result:
            raise libusb1.USBError, result
        return USBDeviceHandle(self.__context, handle)

class LibUSBContext(object):
    """
    libusb1 USB context.

    Provides methods to enumerate & look up USB devices.
    Also provides access to global (device-independent) libusb1 functions.
    """
    __libusb_exit = libusb1.libusb_exit
    __context_p = None
    __added_cb = None
    __removed_cb = None

    def __init__(self):
        """
        Create a new USB context.
        """
        context_p = libusb1.libusb_context_p()
        result = libusb1.libusb_init(byref(context_p))
        if result:
            raise libusb1.USBError, result
        self.__context_p = context_p

    def __del__(self):
        self.exit()

    def exit(self):
        """
        Close (destroy) this USB context.
        """
        context_p = self.__context_p
        if context_p is not None:
            self.__libusb_exit(context_p)
            self.__context_p = None
        self.__added_cb = None
        self.__removed_cb = None

    def getDeviceList(self):
        """
        Return a list of all USB devices currently plugged in, as USBDevice
        instances.
        """
        device_p_p = libusb1.libusb_device_p_p()
        device_list_len = libusb1.libusb_get_device_list(self.__context_p,
                                                         byref(device_p_p))
        result = [USBDevice(self, x) for x in device_p_p[:device_list_len]]
        # XXX: causes problems, why ?
        #libusb1.libusb_free_device_list(device_p_p, 1)
        return result

    def openByVendorIDAndProductID(self, vendor_id, product_id):
        """
        Get the first USB device matching given vendor and product ids.
        Returns an USBDevice instance, or None if no present devide match.
        """
        handle_p = libusb1.libusb_open_device_with_vid_pid(self.__context_p,
            vendor_id, product_id)
        if handle_p:
            result = USBDeviceHandle(self, handle_p)
        else:
            result = None
        return result

    def getPollFDList(self):
        """
        Return file descriptors to be used to poll USB events.
        You should not have to call this method, unless you are integrating
        this class with a polling mechanism.
        """
        pollfd_p_p = libusb1.libusb_get_pollfds(self.__context_p)
        result = []
        append = result.append
        fd_index = 0
        while pollfd_p_p[fd_index]:
            append((pollfd_p_p[fd_index].contents.fd,
                    pollfd_p_p[fd_index].contents.events))
            fd_index += 1
        # XXX: causes problems, why ?
        #libusb1.libusb.free(pollfd_p_p)
        return result

    def handleEvents(self):
        """
        Handle any pending event (blocking).
        See libusb1 documentation for details (there is a timeout, so it's
        not "really" blocking).
        """
        result = libusb1.libusb_handle_events(self.__context_p)
        if result:
            raise libusb1.USBError, result

    def handleEventsTimeout(self, tv=None):
        """
        Handle any pending event (non-blocking).
        tv: for future use. Do not give it a non-None value.
        """
        assert tv is None, 'tv parameter is not supported yet'
        tv = libusb1.timeval(0, 0)
        result = libusb1.libusb_handle_events_timeout(self.__context_p,
            byref(tv))
        if result:
            raise libusb1.USBError, result

    def setPollFDNotifiers(self, added_cb=None, removed_cb=None,
            user_data=None):
        """
        Give libusb1 methods to call when it should add/remove file descriptor
        for polling.
        You should not have to call this method, unless you are integrating
        this class with a polling mechanism.
        """
        if added_cb is None:
            added_cb = POINTER(None)
        else:
            added_cb = libusb1.libusb_pollfd_added_cb_p(added_cb)
        if removed_cb is None:
            removed_cb = POINTER(None)
        else:
            removed_cb = libusb1.libusb_pollfd_removed_cb_p(removed_cb)
        self.__added_cb = added_cb
        self.__removed_cb = removed_cb
        libusb1.libusb_set_pollfd_notifiers(self.__context_p, added_cb,
                                            removed_cb, user_data)

    def getNextTimeout(self):
        """
        Determine the next internal timeout that libusb needs to handle.
        You should not have to call this method, unless you are integrating
        this class with a polling mechanism.
        """
        timeval = libusb1.timeval()
        result = libusb1.libusb_get_next_timeout(self.__context_p,
            byref(timeval))
        if result == 0:
            result = None
        elif result == 1:
            result = timeval.tv_sec + (timeval.tv_usec * 0.000001)
        else:
            raise libusb1.USBError, result
        return result

    def setDebug(self, level):
        """
        Set debugging level.
        Note: depending on libusb compilation settings, this might have no
        effect.
        """
        libusb1.libusb_set_debug(self.__context_p, level)

