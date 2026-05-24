using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.IO.Ports;

namespace QminiUartSimpleDemo
{
    class Program
    {
        static void Main(string[] args)
        {
            //This class abstracts the NioLink protocol. It is implemented below.
            NioLinkUartProtocol iface = new NioLinkUartProtocol();

            try
            {
                //Enter the correct COM port number here
                string portstr = "COM22";

                //Enter the configured baud rate here. The default value is 57600.
                //If you're not sure about the baud rate, please connect your device via USB, open the
                //connection in Waves and check the baud rate in the Device Configuration window.
                int baudRate = 57600;

                sbyte status; //a signed 8-bit integer
                UInt16 availableSpectra; //an unsigned 16-bit integer

                Console.WriteLine("Opening device at port " + portstr + " with " + baudRate + " baud.");               
                iface.OpenPort(portstr, baudRate);

                Console.WriteLine("Device found: " + iface.ReadString(Command_GetModelName) + " with serial number " + iface.ReadString(Command_GetSerialNo));
                Console.WriteLine("Model ID: 0x" + iface.ReadInt(Command_GetDeviceID).ToString("X8"));
                int pixelCount = iface.ReadInt(Command_GetPixelCount);

                iface.Write(Command_Reset); //Reset the device
                status = ReadStatus(iface, out availableSpectra);
                Console.WriteLine("Device resetted. Status is now " + status + ". Available spectra: " + availableSpectra);

                //Read wavelengths from device
                byte[] wavelengthbuffer = iface.ReadData(Command_GetWavelengths); //wavelengths come in as a byte array containing float values
                float[] wavelengths = new float[pixelCount]; //so we create a suitable float array
                float[] spectrum = new float[pixelCount]; //needed later
                System.Buffer.BlockCopy(wavelengthbuffer, 0, wavelengths, 0, pixelCount * 4); //and copy the byte array into it
                Console.WriteLine("Wavelength range: " + wavelengths[0] + " to " + wavelengths[pixelCount - 1] + " nm");

                Console.WriteLine("Setting exposure time to 100 ms.");
                iface.Write(Command_SetExposureTime, 100000);

                Console.WriteLine("Setting averaging to 4x.");
                iface.Write(Command_SetAveraging, 4);
                
                Console.WriteLine("Setting default processing steps.");
                int defaultprocsteps = iface.ReadInt(Command_GetDefaultProcessingSteps);
                iface.Write(Command_SetProcessingSteps, defaultprocsteps);

                for (int i = 0; i < 10; i++)
                {
                    //Start exposure
                    iface.Write(Command_StartExposure, 1); //start one spectrum
                    status = ReadStatus(iface, out availableSpectra);
                    Console.WriteLine("Exposure was started. Status is now " + status + ". Available spectra: " + availableSpectra);

                    //Wait for exposure to be finished
                    DateTime t0 = DateTime.Now;
                    do
                    {
                        status = ReadStatus(iface, out availableSpectra);
                        if (DateTime.Now - t0 > TimeSpan.FromSeconds(2)) throw new Exception("Timeout while waiting for spectrum.");
                    }
                    while (status > (int)SpectrometerStatus.Idle); //Positive values of status indicate the spectrometer is waiting for something to be be finished.
                    Console.WriteLine("Exposure has finished. Status is now " + status + ". Available spectra: " + availableSpectra);

                    //Read out spectrum
                    byte[] spectrumBuffer = iface.ReadData(Command_Get32BitSpectrum); //Read spectrum data into byte buffer
                    //The data consists of a 48-byte header followed by the actual spectrum.
                    //The header contains some metadata about the spectrum as described in the SpectrumHeader structure (see below).
                    float loadLevel = BitConverter.ToSingle(spectrumBuffer, 12); //Here we are only using the LoadLevel value from the header

                    System.Buffer.BlockCopy(spectrumBuffer, 48, spectrum, 0, pixelCount * 4); //Copy the actual spectrum to float array
                    status = ReadStatus(iface, out availableSpectra);
                    Console.WriteLine("Spectrum was read out. Status is now " + status + ". Available spectra: " + availableSpectra);

                    //Calculate and display average spectrum value
                    float avg = 0;
                    for (int j = 0; j < pixelCount; j++) avg += spectrum[j];
                    Console.WriteLine("Average spectrum value: " + (avg / pixelCount) + "   Load level: " + (loadLevel * 100).ToString("0.0") + " %");
                }

                iface.Write(Command_Bye);
            }
            catch (Exception ex)
            {
                Console.WriteLine("An error occurred: " + ex.Message);
            }
            finally
            {
                iface.ClosePort();
                Console.WriteLine("Press Return to exit.");
                Console.Read(); //wait for Return key
            }
        }

        //This method demonstrates how to read and interpret the status value
        private static sbyte ReadStatus(NioLinkUartProtocol iface, out UInt16 availableSpectra)
        {
            UInt32 st = (UInt32)iface.ReadInt(Command_GetStatus); //Read status value from device as unsigned 32 bit integer
            availableSpectra = (UInt16)(st >> 8); //Byte 1 and 2 contain the number of spectra in the device buffer.
            return (sbyte)st; //Byte 0 contains the actual status as a SIGNED integer. (This conversion may need to be done differently in other programming languanges.)
        }

        //Most important command codes for Qseries spectrometers

        private const int Command_GetDeviceID = 0x2000;
        private const int Command_GetSerialNo = 0x2001;
        private const int Command_GetManufacturer = 0x2002;
        private const int Command_GetModelName = 0x2003;
        private const int Command_Reset = 0x0000;
        private const int Command_SetExposureTime = 0x1100;
        private const int Command_GetExposureTime = 0x1000;
        private const int Command_StartExposure = 0x0004;
        private const int Command_CancelExposure = 0x0005;
        private const int Command_GetStatus = 0x3000;
        private const int Command_Get32BitSpectrum = 0x4000;
        private const int Command_GetWavelengths = 0x4001;
        private const int Command_GetMinExposureTime = 0x1200;
        private const int Command_GetMaxExposureTime = 0x1300;
        private const int Command_GetMaxDataValue = 0x2006;
        private const int Command_Bye = 0x0001;
        private const int Command_GetTemperature = 0x3001;
        private const int Command_SetAveraging = 0x1101;
        private const int Command_GetAveraging = 0x1001;
        private const int Command_GetMaxAveraging = 0x1301;
        private const int Command_SetProcessingSteps = 0x1102;
        private const int Command_GetProcessingSteps = 0x1002;
        private const int Command_GetDefaultProcessingSteps = 0x1402;
        private const int Command_GetHardwareVersion = 0x2004;
        private const int Command_GetSoftwareVersion = 0x2005;
        private const int Command_GetPixelCount = 0x2007;
        private const int Command_GetSensorGain = 0x100D;
        private const int Command_SetSensorGain = 0x110D;
        private const int Command_GetMaxSensorGain = 0x130D;
        private const int Command_SetBaudRate = 0x1107;
        private const int Command_GetBaudRate = 0x1007;

        //Status values
        private enum SpectrometerStatus
        {
            Idle = 0,
            WaitingForTrigger = 1,
            TakingSpectrum = 2,
            WaitingForTemperature = 3,  //(Reserved for future use.)

            NotReady = -1, //The sensor is currently in a state in which it cannot take spectra. (Reserved for future use.)
            Busy = -2, //The sensor is currently busy doing something and therefore not ready to take a spectrum.
            Error = -3, //An error occurred while waiting for the exposure to be finished.
            Closed = -4 //The sensor is not initialized or the connection is closed.
        }

        //Spectrum header
        private struct SpectrumHeader
        {
	        UInt32 ExposureTime;       //in us
	        Int32 Averaging;
	        UInt32 TimeStamp; 
	        float LoadLevel;   
	        float Temperature;         //in °C
	        UInt16 PixelCount;        
            UInt16 PixelFormat;   
            UInt16 ProcessingSteps;    //Applied processing steps
            UInt16 IntensityUnit;      //Values from SpectrometerUnits enumeration (see below)
	        Int32 SpectrumDropped;  
	        float SaturationValue;
	        float OffsetAvg;
	        float DarkAvg;
	        float ReadoutNoise;
        }

        private enum SpectrometerUnits
        {
            Unit_Unknown = 0,
            Unit_ADCvalues = 1,
            Unit_ADCnormalized = 2, // ADC values normalized to an exposure time of 1 second
            Unit_nWnm = 3,          // Spectral power in nW/nm
            Unit_mWm2nm = 4,        // Spectral irradiance in mW/m^2/nm
            Unit_Wsrm2nm = 5,       // W/sr/m^2/nm
            Unit_Wsrnm = 6          // W/sr/nm
        };
    }

    //This class implements the NioLink protocol for the UART interface in binary mode.
    
    class NioLinkUartProtocol : Object
    {
        private SerialPort port;
        byte[] buffer = new byte[16384]; //used for sending and receiving data

        //********** Open and close the interface **********

        public void OpenPort(string portname, int BaudRate)
        {
            port = new SerialPort();
            port.PortName = portname;
            port.BaudRate = BaudRate;
            port.Parity = Parity.None;
            port.DataBits = 8;
            port.StopBits = StopBits.One;
            port.ReadTimeout = 4000;
            port.Open();
        }

        public void ClosePort()
        {
            if (port.IsOpen) port.Close();
        }

        //********** The following methods implement the message layer **********

        public void Write(int Command)
        {
            buffer[0] = (byte)Command;
            buffer[1] = (byte)(Command >> 8);
            buffer[2] = 0;
            buffer[3] = 0;
            SendReceiveData(buffer, 4);
        }

        public void Write(int Command, int Value)
        {
            buffer[0] = (byte)Command;
            buffer[1] = (byte)(Command >> 8);
            buffer[2] = 0;
            buffer[3] = 0;
            buffer[4] = (byte)Value;
            buffer[5] = (byte)(Value >> 8);
            buffer[6] = (byte)(Value >> 16);
            buffer[7] = (byte)(Value >> 24);
            SendReceiveData(buffer, 8);
        }

        public void Write(int Command, float Value)
        {
            buffer[0] = (byte)Command;
            buffer[1] = (byte)(Command >> 8);
            buffer[2] = 0;
            buffer[3] = 0;
            byte[] bb = BitConverter.GetBytes(Value);
            buffer[4] = bb[0];
            buffer[5] = bb[1];
            buffer[6] = bb[2];
            buffer[7] = bb[3];
            SendReceiveData(buffer, 8);
        }

        public int ReadInt(int Command)
        {
            buffer[0] = (byte)Command;
            buffer[1] = (byte)(Command >> 8);
            buffer[2] = 0;
            buffer[3] = 0;
            int n = SendReceiveData(buffer, 4);
            return BitConverter.ToInt32(buffer, 4);
        }

        public float ReadFloat(int Command)
        {
            buffer[0] = (byte)Command;
            buffer[1] = (byte)(Command >> 8);
            buffer[2] = 0;
            buffer[3] = 0;
            int n = SendReceiveData(buffer, 4);
            return BitConverter.ToSingle(buffer, 4);
        }

        public string ReadString(int Command)
        {
            buffer[0] = (byte)Command;
            buffer[1] = (byte)(Command >> 8);
            buffer[2] = 0;
            buffer[3] = 0;
            int n = SendReceiveData(buffer, 4);
            if (buffer[n - 1] == 0) n--; //strip null-terminator
            return System.Text.Encoding.ASCII.GetString(buffer, 4, n - 4);
        }

        public byte[] ReadData(int Command) //thread-safe
        {
            buffer[0] = (byte)Command;
            buffer[1] = (byte)(Command >> 8);
            buffer[2] = 0;
            buffer[3] = 0;
            int n = SendReceiveData(buffer, 4);
            byte[] data = new byte[n - 4];
            System.Buffer.BlockCopy(buffer, 4, data, 0, n - 4);
            return data;
        }

        //********** This method implements the interface layer for the UART interface in binary mode **********

        private int SendReceiveData(byte[] InOutBuffer, int NumBytesToSend) //Returns number of bytes received
        {
            SendData(InOutBuffer, NumBytesToSend); //write a message to the device (implemented below)
            int numBytesRead = ReceiveData(InOutBuffer); //read the reply from the device (implemented below)
             
            //Received data must at least include a return code, so it has to be at least 4 bytes.
            if (numBytesRead < 4) throw new Exception("Only " + numBytesRead + " bytes received.");

            //Check return code from message layer
            if (InOutBuffer[0] != 0) throw new Exception("Error code " + InOutBuffer[0] + " received from device.");
            return numBytesRead;
        }

        private byte[] interfaceHeader = new byte[8];

        public void SendData(byte[] buffer, int length)
        {
            // Interface Command: Write = 0xF102
            interfaceHeader[0] = 0x02;
            interfaceHeader[1] = 0xF1;
            interfaceHeader[2] = 0x00;
            interfaceHeader[3] = 0x00;
            // Add payload length (4 bytes)
            interfaceHeader[4] = (byte)(length & 0xFF); //length of message frame LSB
            interfaceHeader[5] = (byte)((length >> 8) & 0xFF); //length  MSB
            interfaceHeader[6] = 0x00; // unused (may be used as checksum later)
            interfaceHeader[7] = 0x00; // unused (may be used as checksum later)
            port.Write(interfaceHeader, 0, 8);

            port.Write(buffer, 0, length); // Write payload data
        }

        private int ReceiveData(byte[] buffer)
        {
            //Read 8 bytes into interfaceHeader. (The SerialPort.Read function does not block, therefore we have to call it repeatedly until all bytes are received.)
            int numBytesRead = 0;
            while (true)
            {
                numBytesRead += port.Read(interfaceHeader, numBytesRead, 8 - numBytesRead);
                if (numBytesRead >= 8) break;
                //Here we could implement a timeout
                System.Threading.Thread.Sleep(1);
            }

            //Check interface return code (first 4 bytes)
            int retcode = BitConverter.ToInt32(interfaceHeader, 0);
            if (retcode != 0) throw new Exception("Interface error 0x" + retcode.ToString("X4") + " returned from device.");

            //Get payload length (following 4 bytes)
            int payloadLength = BitConverter.ToInt32(interfaceHeader, 4);
            if (payloadLength < 4) throw new Exception("Serial port communication error (payload length too small).");

            //Read payload into buffer. (The SerialPort.Read function does not block, therefore we have to call it repeatedly until all bytes are received.)
            numBytesRead = 0;
            while (true)
            {
                numBytesRead += port.Read(buffer, numBytesRead, payloadLength - numBytesRead);
                if (numBytesRead >= payloadLength) break;
                //Here we could implement a timeout
                System.Threading.Thread.Sleep(1);
            }

            return payloadLength;
        }
    }
}
