import cv2
import time
from ultralytics import YOLO

# Point to the FOLDER, not a file inside it
model = YOLO('/home/afful/Downloads/finetuned_ncnn_model', task='detect')

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

print("Camera started. Press 'q' in the video window to quit.\n")

while True:
    ret, frame = cap.read()
    if not ret:
        print("Failed to capture frame")
        break

    start = time.time()
    results = model(frame, imgsz=512, conf=0.25, verbose=False)
    elapsed = (time.time() - start) * 1000

    for box in results[0].boxes:
        name = results[0].names[int(box.cls[0])]
        conf = box.conf[0].item() * 100
        print(f"{name}: {conf:.0f}%  ({elapsed:.0f} ms)")

    cv2.imshow('REPLAST', results[0].plot())
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
