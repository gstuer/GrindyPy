import qwiic_nau7802
import sys
import time
import json
from gpiozero import OutputDevice
import math
from scipy import stats
import statistics

PIN_MOSFET = 17

SCALE_SAMPLES_PER_READING = 2
SCALE_CALIBRATION_FILE = 'calibration.json'

STABILIZATION_THRESHOLD_WEIGHT = 350
STABILIZATION_THRESHOLD_WEIGHT_CHANGE = 0.1
STABILIZATION_SECONDS = 1.0

GRIND_WEIGHT_GOAL = 18
GRIND_MAX_SECONDS = 7
GRIND_WEIGHT_INFLIGHT_DEFAULT = 1.2
GRIND_PREDICTION_FILE = 'prediction.json'
GRIND_COOLDOWN_SECONDS = 1.5

def calibrate(scale: qwiic_nau7802.QwiicNAU7802):
    print("Calibration Mode")
        
    # Calculate zero offset averaged
    print("Remove weight from scale. Press any key to continue...")
    input()
    scale.calculate_zero_offset(10)
    zero_offset = scale.get_zero_offset()

    # Calculate averaged calibration factor 
    print("Place a known weight on the scale and enter weight without units when stable...")
    weight = float(input())
    scale.calculate_calibration_factor(weight, 10)
    calibration_factor = scale.get_calibration_factor()

    # Write calibration values to file
    data = {'offset': zero_offset, 'factor': calibration_factor}
    with open(SCALE_CALIBRATION_FILE, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
    print('Values written to file: ', SCALE_CALIBRATION_FILE)
    print('Calibration complete.')

def get_trimmed_mean_readings(scale: qwiic_nau7802.QwiicNAU7802, samples: int, trim_proportion: float) -> float:
    readings = []
    while len(readings) < samples:
        if scale.available():
            readings.append(scale.get_reading())
            time.sleep(0.001)
    return stats.trim_mean(readings, trim_proportion)

def perform_zero_calibration(scale: qwiic_nau7802.QwiicNAU7802) -> None:
    print("Phase: Zero Calibration")
    print('[Zero Calibration] Fetching sensor readings')
    zero_offset_average = get_trimmed_mean_readings(scale, samples=100, trim_proportion=0.3)
    scale.set_zero_offset(zero_offset_average)
    print(f'[Zero Calibration] Set zero offset to {zero_offset_average}')

def get_weight(scale: qwiic_nau7802.QwiicNAU7802, zero_offset: float, calibration_factor: float) -> float:
    mean_raw = get_trimmed_mean_readings(scale, 1, 0.0)
    mean_weight = (mean_raw - zero_offset) / calibration_factor
    return max(0., mean_weight)

def grind_by_weight(scale: qwiic_nau7802.QwiicNAU7802, mosfet: OutputDevice):
    print("Grind By Weight Mode")

    # Load calibration values from file
    try:
        with open(SCALE_CALIBRATION_FILE, 'r') as file:
            data = json.load(file)
            perform_zero_calibration(scale)
            scale.set_calibration_factor(data['factor'])
    except (FileNotFoundError, KeyError) as error:
        print(f'Calibration file not found or values missing / invalid. Rerun program after calibration: {error}')
        return

    # Load inflight weight from file
    grind_weight_inflight = GRIND_WEIGHT_INFLIGHT_DEFAULT
    try:
        with open(GRIND_PREDICTION_FILE, 'r') as file:
            data = json.load(file)
            grind_weight_inflight = data['inflight']
    except (FileNotFoundError, KeyError) as error:
        print(f'Prediction parameter file not found or values missing / invalid: {error}')
        print('Fallback to default prediction parameters.')
    

    # Arm grind by weight operation
    while True:
        # Phase 1: Threshold Stabilization
        threshold_stable = False
        stable_weight = 0
        print("Phase: Stabilization")
        while not threshold_stable:
            stable_weight = scale.get_weight(samples_to_take = SCALE_SAMPLES_PER_READING)
            if stable_weight >= STABILIZATION_THRESHOLD_WEIGHT:
                begin_time = time.time()
                threshold_stable = True
                while (time.time() - begin_time) < STABILIZATION_SECONDS:
                    current_weight = scale.get_weight(samples_to_take = SCALE_SAMPLES_PER_READING)
                    if abs(stable_weight - current_weight) > STABILIZATION_THRESHOLD_WEIGHT_CHANGE:
                        threshold_stable = False
                        break
                    else:
                        stable_weight = (stable_weight + current_weight) / 2.0
        print(f"[Stabilization] Stable ({stable_weight:.2f} g)")

        # Phase 2: Prediction
        weights = []
        print("Phase: Prediction")
        print("[Prediction] Motor On")
        motor_begin_time = time.time()
        mosfet.on()
        while (time.time() - motor_begin_time) < GRIND_MAX_SECONDS:
            weight = scale.get_weight(samples_to_take = SCALE_SAMPLES_PER_READING) - stable_weight
            weights.append({'weight': weight, 'time': time.time(), 'phase': 'prediction'})
            if weight + grind_weight_inflight >= GRIND_WEIGHT_GOAL:
                break
        mosfet.off()
        motor_end_time = time.time()
        print("[Prediction] Motor Off")

        # Phase 3: Cooldown
        print("Phase: Cooldown")
        while (time.time() - motor_end_time) < GRIND_COOLDOWN_SECONDS:
            weight = scale.get_weight(samples_to_take = SCALE_SAMPLES_PER_READING) - stable_weight
            weights.append({'weight': weight, 'time': time.time(), 'phase': 'cooldown'})

        # Phase 4: Analyzation
        print("Phase: Analyzation")
        timeout = (motor_end_time - motor_begin_time) >= GRIND_MAX_SECONDS
        last_deviation = weights[-1]['weight'] - GRIND_WEIGHT_GOAL
        end_elements = []
        for i in range(1, 6):
            end_elements.append(weights[-1 * i]['weight'] - GRIND_WEIGHT_GOAL)
        average_deviation = statistics.fmean(end_elements)
        print('[Analyzation] Average Deviation: ', average_deviation)
        print('[Analyzation] Last Deviation: ', last_deviation)
        
        # Learn ideal grind inflight weight
        print('[Analyzation] Old Inflight Weight', grind_weight_inflight)
        prediction_parameters = {'inflight_old': grind_weight_inflight, 'inflight': grind_weight_inflight}
        if not timeout:
            grind_weight_inflight = min(max(grind_weight_inflight + average_deviation, 0.5), 2.0)
            prediction_parameters['inflight'] = grind_weight_inflight
            with open(GRIND_PREDICTION_FILE, 'w', encoding='utf-8') as file:
                json.dump(prediction_parameters, file, ensure_ascii=False, indent=4)
            print('[Analyzation] New Inflight Weight', grind_weight_inflight)
        else:
            print('[Analyzation] Timeout')

        # Save grind file
        grind_date_time = time.strftime('%Y-%b-%d_%H:%M:%S', time.localtime(motor_begin_time))
        grind_name = f"Grind_{grind_date_time}"
        grind_data = {'name': grind_name, 'timeout': timeout, 'time_motor_start': motor_begin_time, 'time_motor_stop': motor_end_time, 'prediction_parameters': prediction_parameters, 'weights': weights}
        with open('./grinds/' + grind_name, 'w', encoding='utf-8') as file:
            json.dump(grind_data, file, ensure_ascii=False, indent=4)

        # Phase 5: Removal
        print("Phase: Removal")
        while scale.get_weight(samples_to_take = SCALE_SAMPLES_PER_READING) >= STABILIZATION_THRESHOLD_WEIGHT:
            time.sleep(1)

def monitor(scale: qwiic_nau7802.QwiicNAU7802):
    print("Monitoring Mode")

    print("[Initialization] Calibrate zero offset")
    perform_zero_calibration(scale)

    print("[Initialization] Load & set calibration factor")
    # Load calibration values from file
    try:
        with open(SCALE_CALIBRATION_FILE, 'r') as file:
            calibration_factor = json.load(file)['factor']
            scale.set_calibration_factor(calibration_factor)
    except (FileNotFoundError, KeyError) as error:
        print(f'Calibration file not found or value missing / invalid. Rerun program after calibration: {error}')
        return

    zero_offset = scale.get_zero_offset()
    calibration_factor = scale.get_calibration_factor()

    while True:
        weight = get_weight(scale, zero_offset, calibration_factor)
        print(f'Current Weight: {weight:.2f} g', end='\r', flush=True)

def main():
    # Initialize mosfet controlling motor relay
    mosfet = OutputDevice(PIN_MOSFET)
    mosfet.off()

    # Initialize load cell amplifier
    scale = qwiic_nau7802.QwiicNAU7802()
    if not scale.is_connected():
        print('Load cell amplifier not connected. Leaving program.')
    else:
        scale.begin()
        scale.set_sample_rate(scale.NAU7802_SPS_10)
        scale.set_gain(scale.NAU7802_GAIN_128)
        scale.calibrate_afe()

        try:
            if len(sys.argv) > 1 and sys.argv[1] == 'calibration':
                calibrate(scale)
            elif len(sys.argv) > 1 and sys.argv[1] == 'monitor':
                monitor(scale)
            else:
                grind_by_weight(scale, mosfet)
        except OSError as error:
            mosfet.off()
            print(f'Unexpected OS error: {error}')
            sys.exit(1)
        except (KeyboardInterrupt, SystemExit) as error:
            mosfet.off()
            print(f'Stopping grindy: {error}')
            sys.exit(0)

if __name__ == '__main__':
    main()