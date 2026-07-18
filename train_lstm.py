import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
from lstm_model import DrowsinessLSTM

def generate_synthetic_data(num_samples=1000, seq_len=60):
    """
    Generate synthetic sequence data representing driver state:
    - Alert (Label 0): low EAR_norm, MAR_norm, Pitch_norm, PERCLOS_norm.
    - Drowsy (Label 1): eyes closing, yawning, head nodding, and high PERCLOS.
    """
    X = []
    y = []
    
    half_samples = num_samples // 2
    
    # 1. Alert samples (Class 0)
    for _ in range(half_samples):
        ear_norm = np.random.uniform(0.0, 0.2, seq_len)
        mar_norm = np.random.uniform(0.0, 0.15, seq_len)
        pitch_norm = np.random.uniform(0.0, 0.1, seq_len)
        perclos_norm = np.random.uniform(0.0, 0.1, seq_len)
        
        # Occasional rapid blinks
        for _ in range(np.random.randint(0, 3)):
            start_idx = np.random.randint(5, seq_len - 5)
            ear_norm[start_idx:start_idx+3] = np.random.uniform(0.7, 0.9, 3)
            
        sample = np.stack([ear_norm, mar_norm, pitch_norm, perclos_norm], axis=1)
        X.append(sample)
        y.append(0.0)
        
    # 2. Drowsy / Microsleep samples (Class 1)
    for _ in range(num_samples - half_samples):
        ear_norm = np.random.uniform(0.0, 0.2, seq_len)
        mar_norm = np.random.uniform(0.0, 0.15, seq_len)
        pitch_norm = np.random.uniform(0.0, 0.1, seq_len)
        perclos_norm = np.random.uniform(0.0, 0.1, seq_len)
        
        scenario = np.random.choice(["eyes_closing", "yawning", "head_nodding", "combined"])
        sleepy_start = np.random.randint(20, 35)
        
        if scenario == "eyes_closing" or scenario == "combined":
            ear_norm[sleepy_start:] = np.random.uniform(0.6, 0.95, seq_len - sleepy_start)
            perclos_norm[sleepy_start:] = np.linspace(0.1, 0.8, seq_len - sleepy_start)
            
        if scenario == "yawning" or scenario == "combined":
            mar_norm[sleepy_start:sleepy_start+15] = np.random.uniform(0.7, 1.0, 15)
            
        if scenario == "head_nodding" or scenario == "combined":
            pitch_norm[sleepy_start:] = np.random.uniform(0.5, 0.9, seq_len - sleepy_start)
            
        sample = np.stack([ear_norm, mar_norm, pitch_norm, perclos_norm], axis=1)
        X.append(sample)
        y.append(1.0)
        
    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.float32).reshape(-1, 1)
    
    return X, y

def train():
    print("Generating synthetic dataset...")
    X, y = generate_synthetic_data(num_samples=2000, seq_len=60)
    
    # Shuffle dataset to ensure train/validation sets are balanced and unbiased
    indices = np.arange(len(X))
    np.random.shuffle(indices)
    X = X[indices]
    y = y[indices]
    
    # Split train/test (80/20)
    split_idx = int(0.8 * len(X))
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]
    
    train_dataset = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_dataset = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    
    # Initialize model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = DrowsinessLSTM().to(device)
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    
    epochs = 20
    print(f"Training LSTM model on {device} for {epochs} epochs...")
    
    for epoch in range(epochs):
        model.train()
        train_loss = 0.0
        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            
            optimizer.zero_grad()
            outputs = model(batch_x)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()
            
            train_loss += loss.item() * batch_x.size(0)
            
        train_loss /= len(train_loader.dataset)
        
        # Validation
        model.eval()
        val_loss = 0.0
        correct = 0
        total = 0
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                batch_x, batch_y = batch_x.to(device), batch_y.to(device)
                outputs = model(batch_x)
                loss = criterion(outputs, batch_y)
                val_loss += loss.item() * batch_x.size(0)
                
                preds = (outputs >= 0.5).float()
                correct += (preds == batch_y).sum().item()
                total += batch_y.size(0)
                
        val_loss /= len(val_loader.dataset)
        accuracy = (correct / total) * 100
        
        print(f"Epoch {epoch+1:02d}/{epochs:02d} | Train Loss: {train_loss:.4f} | Val Loss: {val_loss:.4f} | Val Acc: {accuracy:.2f}%")
        
    model_path = "lstm_drowsiness.pth"
    torch.save(model.state_dict(), model_path)
    print(f"Successfully saved model weights to {model_path}")

if __name__ == "__main__":
    train()
